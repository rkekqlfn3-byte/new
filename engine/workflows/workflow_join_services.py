"""Validation and execution services for explicit Excel sheet joins."""

from __future__ import annotations

import re
from collections.abc import Mapping


class JoinPlanValidator:
    """Validate one content-free explicit join contract."""

    FUNCTIONS = frozenset({"sum", "average", "count", "minimum", "maximum"})

    def __init__(self, error_type):
        self.error_type = error_type

    def validate(self, value):
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise self.error_type("시트 조인 계획이 JSON 객체가 아닙니다.")
        required = {"left_sheet", "right_sheet", "left_key", "right_key", "join_type"}
        allowed = required | {"left_aggregation", "right_aggregation"}
        provided = set(value)
        if not required.issubset(provided) or not provided.issubset(allowed):
            raise self.error_type(
                "시트 조인 계획에는 두 시트명·두 키·결합 방식과 선택적인 양쪽 "
                "합계·평균·건수·최솟값·최댓값 집계만 정확히 있어야 합니다."
            )
        plan = {
            "left_sheet": self._name(value.get("left_sheet"), "왼쪽 시트명"),
            "right_sheet": self._name(value.get("right_sheet"), "오른쪽 시트명"),
            "left_key": self._name(value.get("left_key"), "왼쪽 키"),
            "right_key": self._name(value.get("right_key"), "오른쪽 키"),
            "join_type": str(value.get("join_type") or "").strip().casefold(),
        }
        if plan["left_sheet"].casefold() == plan["right_sheet"].casefold():
            raise self.error_type("서로 다른 두 시트를 지정해야 합니다.")
        if plan["join_type"] not in {"inner", "left"}:
            raise self.error_type("조인 방식은 '내부 조인' 또는 '왼쪽 조인'으로 명시해야 합니다.")
        for side in ("left", "right"):
            aggregations = self._aggregations(value, side)
            if aggregations is not None:
                plan[f"{side}_aggregation"] = aggregations
        return plan

    def _name(self, value, label):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or len(text) > 80 or any(ord(character) < 32 for character in text):
            raise self.error_type(f"조인할 {label}은(는) 1~80자의 한 줄 이름이어야 합니다.")
        return text

    def _aggregations(self, value, side):
        field = f"{side}_aggregation"
        if field not in value:
            return None
        label = "왼쪽" if side == "left" else "오른쪽"
        raw = value.get(field)
        single = isinstance(raw, Mapping)
        items = [raw] if single else raw
        if not isinstance(items, list) or not items or len(items) > 5:
            raise self.error_type(f"{label} 집계 계획은 1~5개의 열·집계 방식이어야 합니다.")
        result = []
        identities = set()
        for aggregation in items:
            item = self._aggregation(aggregation, label)
            identity = (item["column"].casefold(), item["function"])
            if identity in identities:
                raise self.error_type(f"같은 {label} 열과 집계 방식을 중복 지정할 수 없습니다.")
            identities.add(identity)
            result.append(item)
        return result[0] if single else result

    def _aggregation(self, value, label):
        if not isinstance(value, Mapping) or set(value) != {"column", "function"}:
            raise self.error_type(f"{label} 집계마다 열과 집계 방식만 있어야 합니다.")
        function = str(value.get("function") or "").strip().casefold()
        if function not in self.FUNCTIONS:
            raise self.error_type(
                f"{label} 집계 방식은 합계·평균·건수·최솟값·최댓값 중 하나여야 합니다."
            )
        return {
            "column": self._name(value.get("column"), f"{label} 집계 열"),
            "function": function,
        }


class ExplicitJoinService:
    """Execute one validated in-memory join and append its derived preview table."""

    FUNCTION_LABELS = {
        "sum": "합계",
        "average": "평균",
        "count": "건수",
        "minimum": "최솟값",
        "maximum": "최댓값",
    }

    def __init__(self, analyzer, validate_plan, error_type, limits):
        self.analyzer = analyzer
        self.validate_plan = validate_plan
        self.error_type = error_type
        self.limits = limits

    def execute(self, profiles, tables, join_plan, *, remaining_table_rows):
        plan = self.validate_plan(join_plan)
        if plan is None:
            return ""
        if len(tables) >= self.limits["worksheets"]:
            raise self.error_type(
                "조인 결과 표를 포함하면 표가 20개를 넘습니다. 표시 시트를 19개 "
                "이하로 줄인 뒤 다시 요청해주세요."
            )
        left = self._profile(profiles, plan["left_sheet"], "왼쪽 시트")
        right = self._profile(profiles, plan["right_sheet"], "오른쪽 시트")
        if left is right:
            raise self.error_type("서로 다른 두 시트를 지정해야 합니다.")
        if max(len(left["rows"]), len(right["rows"])) > self.limits["source_rows"]:
            raise self.error_type(
                f"명시 조인은 각 시트 {self.limits['source_rows']:,}행까지 지원합니다."
            )
        left_data = self._side(left, plan["left_key"], "왼쪽")
        right_data = self._side(right, plan["right_key"], "오른쪽")
        left_data = self._aggregate_if_requested(left_data, plan, "left", "왼쪽")
        if self._aggregation_items(plan, "right") and not left_data["unique"]:
            raise self.error_type(
                "오른쪽 집계 조인은 왼쪽 키가 고유하거나 왼쪽 사전 집계를 "
                "명시했을 때만 지원합니다."
            )
        right_data = self._aggregate_if_requested(right_data, plan, "right", "오른쪽")
        if not left_data["unique"] and not right_data["unique"]:
            raise self.error_type(
                "두 시트의 조인 키가 모두 중복된 다대다 관계라 자동 결합하지 "
                "않습니다. 한쪽 사전 집계를 명시하거나 키를 고유하게 정리해주세요."
            )
        joined = self._join_rows(
            left_data, right_data, plan, remaining_table_rows
        )
        return self._append_result(tables, left_data, right_data, plan, joined)

    @staticmethod
    def _normalized_name(value):
        return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

    def _profile(self, profiles, name, label):
        matches = [
            profile for profile in profiles
            if self._normalized_name(profile["sheet_name"]) == self._normalized_name(name)
        ]
        if len(matches) != 1:
            raise self.error_type(
                f"표시된 Excel 시트에서 {label} '{name}'을(를) 정확히 하나 "
                "찾지 못했습니다. 숨김 여부와 이름을 확인해주세요."
            )
        return matches[0]

    def _column(self, profile, requested, label, *, forbid_measure=False):
        requested_key = self.analyzer._header_key(requested)
        matches = [
            (index, header)
            for index, header in enumerate(profile["headers"])
            if self.analyzer._header_key(header) == requested_key
        ]
        if len(matches) != 1:
            raise self.error_type(
                f"'{profile['sheet_name']}' 시트에서 {label} '{requested}' "
                "열을 정확히 하나 찾지 못했습니다."
            )
        index, header = matches[0]
        if forbid_measure and self.analyzer._looks_like_measure_header(header):
            raise self.error_type(
                f"금액·매출·수량 같은 측정값 열 '{header}'은 조인 키로 사용하지 않습니다."
            )
        return index, str(header)

    def _side(self, profile, requested_key, label):
        key_index, key_header = self._column(
            profile, requested_key, "조인 키", forbid_measure=True
        )
        width = len(profile["headers"])
        rows = [(row + [None] * width)[:width] for row in profile["rows"]]
        keys = [self.analyzer._join_key_value(row[key_index]) for row in rows]
        nonempty = [key for key in keys if key is not None]
        if not nonempty:
            raise self.error_type("지정한 조인 키에 비교할 값이 없습니다.")
        return {
            "profile": profile,
            "headers": list(profile["headers"]),
            "rows": rows,
            "keys": keys,
            "key_index": key_index,
            "key_header": key_header,
            "unique": len(set(nonempty)) == len(nonempty),
            "aggregation": None,
        }

    @staticmethod
    def _aggregation_items(plan, side):
        value = dict(plan or {}).get(f"{side}_aggregation")
        if isinstance(value, Mapping):
            return [dict(value)]
        if isinstance(value, list):
            return [dict(item) for item in value]
        return []

    def _aggregate_if_requested(self, side, plan, side_name, label):
        plans = self._aggregation_items(plan, side_name)
        if not plans:
            return side
        resolved = self._resolved_aggregations(side, plans, label)
        states, raw_keys, input_rows = self._aggregation_states(side, resolved)
        rows, keys = self._aggregation_rows(side, states, raw_keys, resolved)
        result = dict(side)
        result.update({
            "headers": [side["key_header"]] + [
                f"{header} {self.FUNCTION_LABELS[item['function']]}"
                for item, _, header in resolved
            ],
            "rows": rows,
            "keys": keys,
            "key_index": 0,
            "unique": True,
            "aggregation": [
                {
                    "column": header,
                    "function": item["function"],
                    "input_rows": input_rows[index],
                    "groups": len(states),
                }
                for index, (item, _, header) in enumerate(resolved)
            ],
        })
        return result

    def _resolved_aggregations(self, side, plans, label):
        resolved = []
        identities = set()
        for plan in plans:
            index, header = self._column(
                side["profile"], plan["column"], f"{label} 집계 열"
            )
            if index == side["key_index"]:
                raise self.error_type(f"{label} 조인 키와 집계 열은 서로 달라야 합니다.")
            identity = (index, plan["function"])
            if identity in identities:
                raise self.error_type(f"같은 {label} 열과 집계 방식을 중복 지정할 수 없습니다.")
            identities.add(identity)
            resolved.append((plan, index, header))
        return resolved

    def _aggregation_states(self, side, resolved):
        aggregated = {}
        raw_keys = {}
        input_rows = [0] * len(resolved)
        for row, key in zip(side["rows"], side["keys"]):
            if key is None:
                continue
            raw_keys.setdefault(key, row[side["key_index"]])
            states = aggregated.setdefault(key, [
                {"sum": 0.0, "count": 0, "minimum": None, "maximum": None}
                for _ in resolved
            ])
            for index, (plan, column, header) in enumerate(resolved):
                self._update_aggregation_state(
                    states[index], row[column], plan["function"],
                    side["profile"], header, input_rows, index,
                )
        if not aggregated:
            raise self.error_type("집계에 사용할 키가 없습니다.")
        return aggregated, raw_keys, input_rows

    def _update_aggregation_state(
        self, state, value, function, profile, header, input_rows, index
    ):
        if function == "count":
            nonempty = value not in (None, "") and not (
                isinstance(value, str) and not value.strip()
            )
            if nonempty:
                state["count"] += 1
                input_rows[index] += 1
            return
        number = self.analyzer._number(value)
        if number is None:
            raise self.error_type(
                f"'{profile['sheet_name']}' 시트의 {self.FUNCTION_LABELS[function]} "
                f"집계 열 '{header}'에 숫자가 아닌 값이 있습니다."
            )
        state["sum"] += number
        state["count"] += 1
        state["minimum"] = number if state["minimum"] is None else min(state["minimum"], number)
        state["maximum"] = number if state["maximum"] is None else max(state["maximum"], number)
        input_rows[index] += 1

    def _aggregation_rows(self, side, states_by_key, raw_keys, resolved):
        rows, keys = [], []
        for key, states in states_by_key.items():
            values = [raw_keys[key]]
            for state, (plan, _, header) in zip(states, resolved):
                function = plan["function"]
                if function == "average" and state["count"] < 1:
                    raise self.error_type(
                        f"'{side['profile']['sheet_name']}' 시트의 평균 집계 열 "
                        f"'{header}'에 계산할 값이 없습니다."
                    )
                value = {
                    "sum": state["sum"],
                    "average": state["sum"] / state["count"] if state["count"] else None,
                    "minimum": state["minimum"],
                    "maximum": state["maximum"],
                    "count": state["count"],
                }[function]
                values.append(value)
            rows.append(values)
            keys.append(key)
        return rows, keys

    def _join_rows(self, left, right, plan, remaining):
        right_indexes = [
            index for index in range(len(right["headers"]))
            if index != right["key_index"]
        ]
        columns = len(left["headers"]) + len(right_indexes)
        if columns > self.limits["table_columns"]:
            raise self.error_type(
                f"조인 결과가 {self.limits['table_columns']}열을 넘습니다. 필요한 열을 줄인 "
                "별도 시트를 만든 뒤 다시 요청해주세요."
            )
        index = {}
        for row, key in zip(right["rows"], right["keys"]):
            if key is not None:
                index.setdefault(key, []).append([row[i] for i in right_indexes])
        preview_limit = min(self.limits["table_rows"], max(0, int(remaining)))
        output, count, matched, unmatched = [], 0, 0, 0
        for left_row, key in zip(left["rows"], left["keys"]):
            matches = index.get(key, ()) if key is not None else ()
            if matches:
                matched += 1
                candidates = [left_row + list(values) for values in matches]
            elif plan["join_type"] == "left":
                unmatched += 1
                candidates = [left_row + [None] * len(right_indexes)]
            else:
                unmatched += 1
                candidates = []
            count += len(candidates)
            if count > self.limits["output_rows"]:
                raise self.error_type(
                    f"조인 결과가 {self.limits['output_rows']:,}행을 넘어 자동 생성하지 "
                    "않습니다. 키 또는 대상 행을 더 좁혀주세요."
                )
            output.extend(candidates[:max(0, preview_limit - len(output))])
        return {
            "rows": output,
            "count": count,
            "matched": matched,
            "unmatched": unmatched,
            "right_indexes": right_indexes,
            "columns": columns,
        }

    def _append_result(self, tables, left, right, plan, joined):
        cardinality = (
            "one_to_one" if left["unique"] and right["unique"]
            else "one_to_many" if left["unique"] else "many_to_one"
        )
        label = "내부" if plan["join_type"] == "inner" else "왼쪽"
        metadata = {
            "left_sheet": left["profile"]["sheet_name"],
            "right_sheet": right["profile"]["sheet_name"],
            "left_key": left["key_header"],
            "right_key": right["key_header"],
            "join_type": plan["join_type"],
            "cardinality": cardinality,
            "matched_left_rows": joined["matched"],
            "unmatched_left_rows": joined["unmatched"],
            "output_rows": joined["count"],
            "included_rows": len(joined["rows"]),
            "truncated": joined["count"] > len(joined["rows"]),
        }
        for side_name, side in (("left", left), ("right", right)):
            if side["aggregation"] is not None:
                metadata[f"{side_name}_aggregation"] = (
                    side["aggregation"][0]
                    if len(side["aggregation"]) == 1 else side["aggregation"]
                )
        headers = [
            f"{left['profile']['sheet_name']}/{header}" for header in left["headers"]
        ] + [
            f"{right['profile']['sheet_name']}/{right['headers'][index]}"
            for index in joined["right_indexes"]
        ]
        tables.append({
            "name": f"{left['profile']['sheet_name']}↔{right['profile']['sheet_name']} {label} 조인",
            "headers": headers,
            "rows": joined["rows"],
            "total_rows": joined["count"],
            "included_rows": len(joined["rows"]),
            "used_cells": joined["count"] * joined["columns"],
            "derived": True,
            "join": metadata,
        })
        return self._summary(left, right, label, joined["count"])

    def _summary(self, left, right, label, count):
        key = f"'{left['key_header']}'"
        if left["key_header"] != right["key_header"]:
            key += f" ↔ '{right['key_header']}'"
        descriptions = []
        for side_label, side in (("왼쪽", left), ("오른쪽", right)):
            if side["aggregation"] is not None:
                parts = [
                    f"'{item['column']}' {self.FUNCTION_LABELS[item['function']]}"
                    for item in side["aggregation"]
                ]
                descriptions.append(
                    f"{side_label} '{side['profile']['sheet_name']}'의 {' · '.join(parts)}"
                )
        aggregation = f", {' · '.join(descriptions)}를 키별 사전 집계한 뒤" if descriptions else ""
        return (
            f"승인한 {label} 조인: '{left['profile']['sheet_name']}'과 "
            f"'{right['profile']['sheet_name']}'을 {key} 키로{aggregation} 결합해 "
            f"{count:,}행을 만들었으며 Excel 원본은 변경하지 않았습니다."
        )

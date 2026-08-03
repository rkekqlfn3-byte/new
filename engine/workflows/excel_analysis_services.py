"""Focused services used by the Excel business-workflow analyzer."""

from __future__ import annotations

from collections import Counter
from pathlib import Path


class RelationshipAnalysisService:
    """Detect and attach bounded, schema-only relationships between tables."""

    CARDINALITY_RANK = {
        "one_to_one": 0,
        "one_to_many": 1,
        "many_to_one": 1,
        "many_to_many": 2,
    }

    def __init__(self, analyzer, max_relationships):
        self.analyzer = analyzer
        self.limit = max_relationships
        self.profile_cache = {}

    def apply(self, profiles):
        detected = []
        for left_index, left in enumerate(profiles):
            for right_index, right in enumerate(
                profiles[left_index + 1:], start=left_index + 1
            ):
                candidates = self._pair_candidates(
                    left_index, left, right_index, right
                )
                for relationship in candidates[:self.limit]:
                    detected.append((left, relationship))
        detected.sort(key=self._detected_sort_key)
        return self._attach_and_describe(detected[:self.limit])

    def _column_profile(self, profile_index, profile, column):
        key = (profile_index, column)
        if key not in self.profile_cache:
            self.profile_cache[key] = self.analyzer._relation_column_profile(
                profile["rows"], column
            )
        return self.profile_cache[key]

    def _pair_candidates(self, left_index, left, right_index, right):
        left_headers = self._header_map(left)
        right_headers = self._header_map(right)
        candidates = []
        for left_key, left_items in left_headers.items():
            if not left_key or len(left_items) != 1:
                continue
            left_column, left_header = left_items[0]
            for right_key, right_items in right_headers.items():
                if not right_key or len(right_items) != 1:
                    continue
                right_column, right_header = right_items[0]
                candidate = self._candidate(
                    left_index,
                    left,
                    left_column,
                    left_header,
                    left_key,
                    right_index,
                    right,
                    right_column,
                    right_header,
                    right_key,
                )
                if candidate is not None:
                    candidates.append(candidate)
        self._mark_ambiguity(candidates)
        candidates.sort(key=self._candidate_sort_key)
        return candidates

    def _header_map(self, profile):
        result = {}
        for column, header in enumerate(profile["headers"]):
            result.setdefault(self.analyzer._header_key(header), []).append(
                (column, header)
            )
        return result

    def _candidate(
        self,
        left_index,
        left,
        left_column,
        left_header,
        left_key,
        right_index,
        right,
        right_column,
        right_header,
        right_key,
    ):
        same_header = left_key == right_key
        if not same_header and not (
            self.analyzer._looks_like_key_header(left_header)
            and self.analyzer._looks_like_key_header(right_header)
        ):
            return None
        if self.analyzer._looks_like_measure_header(left_header) or self.analyzer._looks_like_measure_header(right_header):
            return None
        left_profile = self._column_profile(left_index, left, left_column)
        right_profile = self._column_profile(right_index, right, right_column)
        stats = self._overlap_stats(left_profile, right_profile)
        if stats is None or not self._safe_candidate(same_header, left_header, stats):
            return None
        cardinality = self._cardinality(stats["left_unique"], stats["right_unique"])
        if not same_header and cardinality == "many_to_many":
            return None
        return {
            "other_sheet": right["sheet_name"],
            "column": str(left_header),
            "other_column": str(right_header),
            "match_basis": "normalized_header" if same_header else "value_overlap",
            "left_column_index": left_column,
            "right_column_index": right_column,
            "cardinality": cardinality,
            "matched_key_count": stats["matched"],
            "left_distinct_count": len(stats["left_values"]),
            "right_distinct_count": len(stats["right_values"]),
            "left_coverage": round(stats["left_coverage"], 4),
            "right_coverage": round(stats["right_coverage"], 4),
            "sample_limited": bool(left_profile["sample_limited"] or right_profile["sample_limited"]),
        }

    @staticmethod
    def _overlap_stats(left, right):
        left_values, right_values = left["values"], right["values"]
        if not left_values or not right_values:
            return None
        matched = len(left_values & right_values)
        if matched <= 0:
            return None
        left_ratio = left["unique_count"] / left["nonempty_count"]
        right_ratio = right["unique_count"] / right["nonempty_count"]
        return {
            "left_values": left_values,
            "right_values": right_values,
            "matched": matched,
            "left_coverage": matched / len(left_values),
            "right_coverage": matched / len(right_values),
            "left_ratio": left_ratio,
            "right_ratio": right_ratio,
            "left_unique": left_ratio >= 0.98,
            "right_unique": right_ratio >= 0.98,
        }

    def _safe_candidate(self, same_header, header, stats):
        if same_header and (
            max(stats["left_coverage"], stats["right_coverage"]) < 0.5
            or (
                not self.analyzer._looks_like_key_header(header)
                and max(stats["left_ratio"], stats["right_ratio"]) < 0.8
            )
        ):
            return False
        return same_header or (
            stats["matched"] >= 3
            and min(stats["left_coverage"], stats["right_coverage"]) >= 0.8
            and (stats["left_unique"] or stats["right_unique"])
        )

    @staticmethod
    def _cardinality(left_unique, right_unique):
        if left_unique and right_unique:
            return "one_to_one"
        if left_unique:
            return "one_to_many"
        if right_unique:
            return "many_to_one"
        return "many_to_many"

    @staticmethod
    def _mark_ambiguity(candidates):
        left_counts = Counter(item["left_column_index"] for item in candidates)
        right_counts = Counter(item["right_column_index"] for item in candidates)
        for item in candidates:
            item["ambiguous"] = bool(
                item["match_basis"] == "value_overlap"
                and (
                    left_counts[item["left_column_index"]] > 1
                    or right_counts[item["right_column_index"]] > 1
                )
            )
            item.pop("left_column_index", None)
            item.pop("right_column_index", None)

    def _candidate_sort_key(self, item):
        return (
            item["match_basis"] != "normalized_header",
            item["ambiguous"],
            item["sample_limited"],
            self.CARDINALITY_RANK[item["cardinality"]],
            -min(item["left_coverage"], item["right_coverage"]),
            -item["matched_key_count"],
            item["column"].casefold(),
            item["other_column"].casefold(),
        )

    def _detected_sort_key(self, entry):
        left, item = entry
        return (
            item["match_basis"] != "normalized_header",
            item["ambiguous"],
            item["sample_limited"],
            self.CARDINALITY_RANK[item["cardinality"]],
            -min(item["left_coverage"], item["right_coverage"]),
            -item["matched_key_count"],
            left["sheet_name"].casefold(),
            item["other_sheet"].casefold(),
            item["column"].casefold(),
            item["other_column"].casefold(),
        )

    @staticmethod
    def _attach_and_describe(detected):
        labels = {
            "one_to_one": "1:1",
            "one_to_many": "1:N",
            "many_to_one": "N:1",
            "many_to_many": "N:M",
        }
        insights = []
        for left, relationship in detected:
            left["table"].setdefault("relationships", []).append(relationship)
            key = (
                f"'{relationship['column']}' 열"
                if relationship["match_basis"] == "normalized_header"
                else f"'{relationship['column']}' ↔ '{relationship['other_column']}' 열"
            )
            review = " 이름이 다른 열이므로 사용자 확인이 필요합니다." if relationship["match_basis"] == "value_overlap" else ""
            insights.append(
                f"시트 관계 후보: {left['sheet_name']} ↔ {relationship['other_sheet']}의 "
                f"{key}에서 {relationship['matched_key_count']}개 키가 겹치며 관계 형태는 "
                f"{labels[relationship['cardinality']]}입니다.{review}"
            )
        return insights


class RelationshipCandidateExportService:
    """Convert attached relationship metadata into the public safe schema."""

    def __init__(self, analyzer, max_relationships):
        self.analyzer = analyzer
        self.limit = max_relationships

    def build(self, context):
        product = self.analyzer.run({
            "source_path": context["source_path"],
            "title": "Excel 시트 관계 후보 검사",
            "preferences": {"summary_lines": 20},
            "join_plan": None,
            "source_scope": None,
        })
        candidates = []
        for table in list(product.get("tables") or []):
            if table.get("derived"):
                continue
            for relationship in list(table.get("relationships") or []):
                candidate = self._candidate(table, relationship)
                if candidate is not None:
                    candidates.append(candidate)
        candidates.sort(key=self._sort_key)
        candidates = candidates[:self.limit]
        return {
            "status": "candidate_found" if candidates else "no_candidate",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "automatic_execution_allowed": False,
            "raw_cell_values_stored": False,
            "document_paths_reported": False,
        }

    @staticmethod
    def _candidate(table, relationship):
        item = dict(relationship or {})
        left = str(table.get("name") or "").strip()
        right = str(item.get("other_sheet") or "").strip()
        left_key = str(item.get("column") or "").strip()
        right_key = str(item.get("other_column") or left_key).strip()
        basis = str(item.get("match_basis") or "").strip()
        cardinality = str(item.get("cardinality") or "").strip()
        if not all((left, right, left_key, right_key)) or basis not in {"normalized_header", "value_overlap"} or cardinality not in {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}:
            return None
        left_coverage = float(item.get("left_coverage") or 0.0)
        right_coverage = float(item.get("right_coverage") or 0.0)
        ambiguous = bool(item.get("ambiguous"))
        limited = bool(item.get("sample_limited"))
        requires = cardinality == "many_to_many"
        confidence = "high" if basis == "normalized_header" and not ambiguous and not limited and min(left_coverage, right_coverage) >= 0.8 and not requires else "review_required"
        return {
            "left_sheet": left,
            "right_sheet": right,
            "left_key": left_key,
            "right_key": right_key,
            "match_basis": basis,
            "ambiguous": ambiguous,
            "cardinality": cardinality,
            "matched_key_count": int(item.get("matched_key_count") or 0),
            "left_distinct_count": int(item.get("left_distinct_count") or 0),
            "right_distinct_count": int(item.get("right_distinct_count") or 0),
            "left_coverage": round(left_coverage, 4),
            "right_coverage": round(right_coverage, 4),
            "sample_limited": limited,
            "requires_preaggregation": requires,
            "confidence": confidence,
        }

    @staticmethod
    def _sort_key(item):
        rank = {"one_to_one": 0, "one_to_many": 1, "many_to_one": 1, "many_to_many": 2}
        return (
            item["match_basis"] != "normalized_header",
            item["ambiguous"],
            item["sample_limited"],
            rank[item["cardinality"]],
            -min(item["left_coverage"], item["right_coverage"]),
            -item["matched_key_count"],
            item["left_sheet"].casefold(),
            item["right_sheet"].casefold(),
            item["left_key"].casefold(),
        )


class PivotInsightService:
    """Derive one bounded category/measure summary per eligible sheet."""

    def __init__(self, analyzer, format_number, max_summaries, max_groups):
        self.analyzer = analyzer
        self.format_number = format_number
        self.max_summaries = max_summaries
        self.max_groups = max_groups

    def apply(self, profiles):
        insights = []
        for profile in profiles:
            if len(insights) >= self.max_summaries:
                break
            summary = self._summary(profile)
            if summary is None:
                continue
            profile["table"].setdefault("pivot_summaries", []).append(summary)
            top = summary["groups"][0]
            insights.append(
                f"{profile['sheet_name']} 시트의 '{summary['group_by']}'별 "
                f"'{summary['value_column']}' 합계는 '{top['label']}'이 "
                f"{self.format_number(top['sum'])}으로 가장 큽니다."
            )
        return insights

    def _summary(self, profile):
        headers, rows = list(profile["headers"]), list(profile["rows"])
        categories, measures = self._columns(headers, rows)
        if not categories or not measures:
            return None
        category = categories[0]
        measure = max(measures, key=lambda item: (item[1], -item[0]))[0]
        groups = self._groups(rows, category, measure)
        if len(groups) < 2:
            return None
        ranked = sorted(groups.values(), key=lambda item: (-item["sum"], item["label"].casefold()))
        top = [{"label": item["label"], "count": int(item["count"]), "sum": round(item["sum"], 6)} for item in ranked[:self.max_groups]]
        return {
            "group_by": str(headers[category]),
            "value_column": str(headers[measure]),
            "group_count": len(groups),
            "groups": top,
            "truncated": len(groups) > self.max_groups,
        }

    def _columns(self, headers, rows):
        categories, measures = [], []
        for column, header in enumerate(headers):
            values = [row[column] for row in rows if column < len(row) and row[column] not in (None, "")]
            if not values:
                continue
            numbers = [number for value in values for number in [self.analyzer._number(value)] if number is not None]
            labels = {str(value).strip().casefold() for value in values if str(value).strip()}
            if 2 <= len(labels) <= 20 and len(numbers) < len(values) / 2:
                categories.append(column)
            if numbers and self.analyzer._looks_like_measure_header(header):
                measures.append((column, len(numbers)))
        return categories, measures

    def _groups(self, rows, category_column, measure_column):
        groups = {}
        for row in rows:
            category = row[category_column] if category_column < len(row) else None
            number = self.analyzer._number(row[measure_column] if measure_column < len(row) else None)
            label = ("" if category is None else str(category)).strip()
            if not label or number is None:
                continue
            group = groups.setdefault(label.casefold(), {"label": label[:80], "count": 0, "sum": 0.0})
            group["count"] += 1
            group["sum"] += number
        return groups


class ExcelWorkbookAnalysisService:
    """Read an exact workbook and build the bounded common work-product model."""

    def __init__(self, analyzer, dependencies, limits):
        self.analyzer = analyzer
        self.dep = dependencies
        self.limits = limits

    def run(self, context):
        from engine.app_actions.com_lifecycle import com_apartment

        source = self.dep["absolute_path"](context["source_path"])
        join_plan = self.dep["validate_join"](context.get("join_plan"))
        scope = self.dep["validate_scope"](context.get("source_scope"))
        if join_plan is not None and scope is not None:
            raise self.dep["scope_error"]("시트 조인과 선택 범위만 분석은 함께 실행할 수 없습니다.")
        lease = None
        with com_apartment(self.analyzer._com_runtime):
            try:
                lease, workbook = self.analyzer._open(source)
                self._validate_workbook(workbook, source)
                visible = self._visible_sheets(workbook)
                ranges = self._sheet_ranges(visible, scope)
                model = self._analyze_ranges(ranges, join_plan)
                return self._product(context, source, join_plan, model)
            finally:
                if lease is not None:
                    lease.cleanup()

    def _validate_workbook(self, workbook, source):
        if self.dep["path_key"](self.analyzer._workbook_path(workbook)) != self.dep["path_key"](source):
            raise self.dep["error"]("연결된 Excel 원본과 다른 통합문서는 분석하지 않습니다.")

    def _visible_sheets(self, workbook):
        worksheets = workbook.Worksheets
        visible = []
        for index in range(1, int(worksheets.Count) + 1):
            sheet = worksheets.Item(index)
            try:
                shown = int(getattr(sheet, "Visible", -1)) == -1
            except Exception:
                shown = True
            if shown:
                visible.append(sheet)
        if not visible:
            raise self.dep["error"]("표시된 Excel 시트가 없어 분석할 수 없습니다.")
        return visible

    def _sheet_ranges(self, visible, scope):
        if scope is not None:
            return [self._scope_range(visible, scope)]
        if len(visible) > self.limits["worksheets"]:
            raise self.dep["error"](
                f"한 번에 분석할 수 있는 표시 시트는 {self.limits['worksheets']}개까지입니다."
            )
        ranges, total = [], 0
        for worksheet in visible:
            name = str(getattr(worksheet, "Name", "") or "Sheet")
            used = worksheet.UsedRange
            rows, columns = int(used.Rows.Count), int(used.Columns.Count)
            if rows < 1 or columns < 1:
                continue
            cells = rows * columns
            self._validate_range_size(name, columns, cells)
            total += cells
            if total > self.limits["source_cells"]:
                raise self.dep["error"](
                    f"표시 시트 전체 분석 범위는 {self.limits['source_cells']:,}셀까지입니다."
                )
            ranges.append((worksheet, used, name, rows, columns, cells, None))
        return ranges

    def _scope_range(self, visible, scope):
        matches = [sheet for sheet in visible if str(getattr(sheet, "Name", "") or "").casefold() == scope["sheet_name"].casefold()]
        if len(matches) != 1:
            raise self.dep["scope_error"](
                f"표시된 Excel 시트에서 선택 범위의 시트 '{scope['sheet_name']}'을 정확히 찾지 못했습니다."
            )
        sheet = matches[0]
        name = str(getattr(sheet, "Name", "") or "Sheet")
        used = sheet.Range(scope["address"])
        rows, columns = int(used.Rows.Count), int(used.Columns.Count)
        cells = rows * columns
        if rows < 2 or columns < 1 or columns > self.limits["table_columns"] or cells > self.limits["sheet_cells"]:
            raise self.dep["scope_error"]("승인된 선택 범위의 실제 크기가 안전 한도와 다릅니다.")
        return sheet, used, f"{name}!{scope['address']}", rows, columns, cells, dict(scope)

    def _validate_range_size(self, name, columns, cells):
        if cells > self.limits["sheet_cells"]:
            raise self.dep["error"](
                f"'{name}' 시트는 {self.limits['sheet_cells']:,}셀을 넘어 한 번에 분석할 수 없습니다."
            )
        if columns > self.limits["table_columns"]:
            raise self.dep["error"](
                f"'{name}' 시트는 {self.limits['table_columns']}열을 넘어 한 번에 분석할 수 없습니다."
            )

    def _analyze_ranges(self, ranges, join_plan):
        metrics, tables, charts, profiles = [], [], [], []
        remaining = self.limits["total_table_rows"] - (self.limits["table_rows"] if join_plan is not None else 0)
        qualify_metrics = len(ranges) > 1
        for worksheet, used, name, rows, columns, cells, scope in ranges:
            matrix = self.analyzer._matrix(used.Value2, rows, columns)
            if not matrix or not any(item not in (None, "") for row in matrix for item in row):
                continue
            headers = [str(value).strip() if value not in (None, "") else f"열 {index}" for index, value in enumerate(matrix[0], 1)]
            data_rows = matrix[1:]
            self._metrics(metrics, name, headers, data_rows, qualify_metrics)
            included = min(len(data_rows), self.limits["table_rows"], remaining)
            table_rows = [(row + [None] * columns)[:columns] for row in data_rows[:included]]
            remaining -= included
            table = {"name": name, "headers": headers, "rows": table_rows, "total_rows": max(0, rows - 1), "included_rows": len(table_rows), "used_cells": cells}
            if scope is not None:
                table["source_scope"] = dict(scope)
            tables.append(table)
            profiles.append({"sheet_name": name, "headers": headers, "rows": data_rows, "table": table})
            if scope is None:
                charts.extend(self.analyzer._chart_data(worksheet, self.limits["charts"] - len(charts)))
        if not tables:
            raise self.dep["error"]("표시된 Excel 시트에 분석할 데이터가 없습니다.")
        return {"metrics": metrics, "tables": tables, "charts": charts, "profiles": profiles}

    def _metrics(self, output, sheet, headers, rows, qualified):
        for column, header in enumerate(headers):
            if len(output) >= self.limits["metrics"]:
                break
            numbers = [number for row in rows for number in [self.analyzer._number(row[column] if column < len(row) else None)] if number is not None]
            if numbers:
                output.append({
                    "name": f"{sheet}/{header}" if qualified else header,
                    "sheet_name": sheet,
                    "column_name": header,
                    "count": len(numbers),
                    "sum": round(sum(numbers), 6),
                    "average": round(sum(numbers) / len(numbers), 6),
                    "minimum": min(numbers),
                    "maximum": max(numbers),
                })

    def _product(self, context, source, join_plan, model):
        tables, profiles = model["tables"], model["profiles"]
        join_insight = self.analyzer._apply_explicit_join(
            profiles,
            tables,
            join_plan,
            remaining_table_rows=self.limits["total_table_rows"] - sum(int(table.get("included_rows") or 0) for table in tables),
        ) if join_plan is not None else ""
        insights = (
            ([join_insight] if join_insight else [])
            + self.analyzer._relationship_insights(profiles)
            + self.analyzer._pivot_insights(profiles)
            + [f"{item['name']} 합계는 {item['sum']:,}, 평균은 {item['average']:,}입니다." for item in model["metrics"]]
        )
        summary_lines = max(1, min(int(context.get("preferences", {}).get("summary_lines", 8)), 20))
        insights = insights[:summary_lines]
        if not insights:
            insights.append("숫자형 열이 없어 표 구조와 원본 행 수를 중심으로 정리했습니다.")
        return self.dep["product"](
            title=str(context.get("title") or f"{Path(source).stem} 분석"),
            metrics=model["metrics"],
            tables=tables,
            charts=model["charts"],
            insights=insights,
            source_files=[source],
        ).to_dict()

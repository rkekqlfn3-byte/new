import unittest

from engine.workflow_step_registry import (
    pdf_workflow_step_recipe,
    registered_workflow_step_names,
    report_workflow_step_order,
    report_workflow_step_recipe,
    validate_pdf_workflow_step_recipe,
    validate_report_workflow_step_recipe,
)


class WorkflowStepRegistryTests(unittest.TestCase):
    def test_registered_recipes_are_sequential_content_free_allowlists(self):
        self.assertTrue(
            {
                "analyze_excel",
                "create_word_report",
                "create_hwp_report",
                "create_powerpoint_summary",
            }.issubset(registered_workflow_step_names())
        )
        for report_format in ("word", "hwp", "both"):
            with self.subTest(report_format=report_format):
                order = report_workflow_step_order(report_format)
                recipe = report_workflow_step_recipe(report_format)
                self.assertEqual(list(order), [item["step_name"] for item in recipe])
                self.assertEqual([], recipe[0]["depends_on"])
                for index, item in enumerate(recipe[1:], 1):
                    self.assertEqual(
                        [recipe[index - 1]["step_name"]],
                        item["depends_on"],
                    )
                serialized = str(recipe).casefold()
                self.assertNotIn("path", serialized)
                self.assertNotIn("content", serialized)

    def test_pdf_recipes_are_content_free_and_exactly_allowlisted(self):
        outputs = ("word", "hwp", "powerpoint", "excel")
        recipe = pdf_workflow_step_recipe(outputs)

        self.assertEqual("analyze_pdf", recipe[0]["step_name"])
        self.assertEqual(
            [
                "create_word_report_from_pdf",
                "create_hwp_report_from_pdf",
                "create_powerpoint_from_pdf",
                "create_excel_from_pdf_table",
            ],
            [item["step_name"] for item in recipe[1:]],
        )
        self.assertNotIn("path", str(recipe).casefold())
        self.assertNotIn("document_text", str(recipe).casefold())
        self.assertEqual(recipe, validate_pdf_workflow_step_recipe(recipe, outputs))
        mutated = [dict(item) for item in recipe]
        mutated[-1]["effect"] = "modify_source"
        with self.assertRaises(ValueError):
            validate_pdf_workflow_step_recipe(mutated, outputs)

    def test_recipe_validation_rejects_unknown_reordered_or_mutated_steps(self):
        mutations = []
        unknown = report_workflow_step_recipe("word")
        unknown[1]["step_name"] = "run_arbitrary_code"
        mutations.append(unknown)
        reordered = report_workflow_step_recipe("word")
        reordered[1:3] = reversed(reordered[1:3])
        mutations.append(reordered)
        changed_effect = report_workflow_step_recipe("word")
        changed_effect[1]["effect"] = "modify_source"
        mutations.append(changed_effect)
        extra_field = report_workflow_step_recipe("word")
        extra_field[1]["command"] = "secret"
        mutations.append(extra_field)

        for recipe in mutations:
            with self.subTest(recipe=recipe):
                with self.assertRaisesRegex(ValueError, "허용 목록"):
                    validate_report_workflow_step_recipe(recipe, "word")

    def test_report_only_recipes_exclude_only_the_registered_presentation_step(self):
        for report_format, expected in (
            ("word", ("analyze_excel", "create_word_report")),
            ("hwp", ("analyze_excel", "create_hwp_report")),
            (
                "both",
                (
                    "analyze_excel",
                    "create_word_report",
                    "create_hwp_report",
                ),
            ),
        ):
            with self.subTest(report_format=report_format):
                self.assertEqual(
                    expected,
                    report_workflow_step_order(report_format, False),
                )
                recipe = report_workflow_step_recipe(report_format, False)
                self.assertEqual(expected, tuple(
                    item["step_name"] for item in recipe
                ))
                self.assertEqual(
                    recipe,
                    validate_report_workflow_step_recipe(
                        recipe,
                        report_format,
                        False,
                    ),
                )
                with self.assertRaisesRegex(ValueError, "허용 목록"):
                    validate_report_workflow_step_recipe(
                        report_workflow_step_recipe(report_format, True),
                        report_format,
                        False,
                    )

        with self.assertRaisesRegex(ValueError, "참/거짓"):
            report_workflow_step_order("word", 1)

    def test_presentation_only_recipe_excludes_every_report_step(self):
        for report_format in ("word", "hwp", "both"):
            with self.subTest(report_format=report_format):
                order = report_workflow_step_order(
                    report_format,
                    True,
                    False,
                )
                recipe = report_workflow_step_recipe(
                    report_format,
                    True,
                    False,
                )
                self.assertEqual(
                    ("analyze_excel", "create_powerpoint_summary"),
                    order,
                )
                self.assertEqual(
                    list(order),
                    [item["step_name"] for item in recipe],
                )
                self.assertEqual(
                    recipe,
                    validate_report_workflow_step_recipe(
                        recipe,
                        report_format,
                        True,
                        False,
                    ),
                )

        with self.assertRaisesRegex(ValueError, "모두 제외"):
            report_workflow_step_order("word", False, False)


if __name__ == "__main__":
    unittest.main()

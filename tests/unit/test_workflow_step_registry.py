import unittest

from engine.workflow_step_registry import (
    registered_workflow_step_names,
    report_workflow_step_order,
    report_workflow_step_recipe,
    validate_report_workflow_step_recipe,
)


class WorkflowStepRegistryTests(unittest.TestCase):
    def test_registered_recipes_are_sequential_content_free_allowlists(self):
        self.assertEqual(
            (
                "analyze_excel",
                "create_word_report",
                "create_hwp_report",
                "create_powerpoint_summary",
            ),
            registered_workflow_step_names(),
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


if __name__ == "__main__":
    unittest.main()


from django.test import SimpleTestCase

from apps.reminders.services.templating import TemplateError, render_html_block, render_text, validate_template_text


class TemplatingTests(SimpleTestCase):
    def test_known_placeholders_render(self):
        self.assertEqual(render_text("Hi {{ unit_name }} / {{department_name}}", {"unit_name": "P1", "department_name": "QA"}), "Hi P1 / QA")

    def test_unknown_placeholder_rejected(self):
        self.assertTrue(validate_template_text("{{ secret_key }}"))
        with self.assertRaises(TemplateError):
            render_text("{{ secret_key }}", {})

    def test_attribute_access_and_code_not_supported(self):
        self.assertTrue(validate_template_text("{{ user.password }}"))
        self.assertTrue(validate_template_text("{% for x in y %}{{ x }}{% endfor %}"))

    def test_unmatched_braces_rejected(self):
        self.assertTrue(validate_template_text("Hello {{ unit_name"))

    def test_missing_value_uses_placeholder(self):
        self.assertEqual(render_text("{{ unit_name }}", {}), "-")

    def test_html_block_escapes_dynamic_values_and_admin_text(self):
        html = render_html_block("<b>Hi</b> {{ unit_name }}", {"unit_name": "<script>alert(1)</script>"})
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;", html)

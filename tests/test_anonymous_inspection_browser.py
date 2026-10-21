"""Browser integration with every request fulfilled locally; no Instagram access."""
import unittest

from playwright.sync_api import sync_playwright

from backend.experimental.anonymous_inspection import collect, control_diagnostic


HTML = '''<!doctype html><title>example_target</title>
<script type="application/json">{"username":"example_target","id":"100"}</script>
<a href="/example_target/followers/" onclick="openList(event)">335 followers</a>
<a href="/example_target/following/" onclick="openList(event)">511 following</a>
<script>
function openList(event) {
  event.preventDefault();
  const dialog = document.createElement('div');
  dialog.setAttribute('role', 'dialog');
  const heading = document.createElement('h2');
  heading.textContent = event.currentTarget.getAttribute('href').includes('/followers/') ? 'Followers' : 'Following';
  dialog.append(heading);
  for (let i=0; i<15; i++) {
    const a = document.createElement('a');
    a.href = '/fixture_' + i + '/'; a.textContent = 'fixture_' + i;
    a.style.display = 'block'; dialog.append(a);
  }
  document.body.append(dialog);
}
document.addEventListener('keydown', e => {
  if(e.key === 'Escape') document.querySelector('[role="dialog"]')?.remove();
});
</script>'''


class OfflineBrowserInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def run_fixture(self, html):
        context = self.browser.new_context(service_workers="block")
        intercepted = []

        def fixture(route):
            intercepted.append(route.request.resource_type)
            route.fulfill(status=200, content_type="text/html", body=html)

        context.route("**/*", fixture)
        try:
            result = collect(context.new_page())
        finally:
            context.close()
        self.assertTrue(intercepted)
        return result

    def test_initial_document_and_both_ui_lists(self):
        result = self.run_fixture(HTML)
        self.assertIsNone(result["error"])
        self.assertEqual(result["network"]["responses"][0]["start_phase"], "initial")
        self.assertEqual(result["network"]["responses"][0]["candidate_objects"], 1)
        for direction in ("followers", "following"):
            self.assertEqual(len(result[direction]["rows"]), 10)
            self.assertEqual(len(result[direction]["cycles"]), 1)
            self.assertEqual(result[direction]["state"], "incomplete")
        self.assertIsNone(result["non_followers"])

    def test_login_dialog_stops_before_following(self):
        html = HTML.replace("event.preventDefault();", "event.preventDefault(); document.body.insertAdjacentHTML('beforeend', '<input type=\"password\">');")
        result = self.run_fixture(html)
        self.assertEqual(result["error"], "authentication_required")
        self.assertIsNone(result["following"])
        self.assertEqual(result["followers"]["reason"], "operation_interrupted")

    def test_overlay_is_diagnosed_without_forcing_click_or_saving_text(self):
        result = self.run_fixture(HTML + '<div style="position:fixed;inset:0;z-index:9999">SECRET fixture overlay</div>')
        self.assertEqual(result["error"], "ui_timeout")
        self.assertIn("pointer_intercepted", result["click_failure_hints"])
        self.assertFalse(result["control_after_failure"]["center_hits_control"])
        self.assertIsNone(result["following"])
        import json
        self.assertNotIn("SECRET", json.dumps(result))

    def test_disabled_control_is_diagnosed_without_enabling_it(self):
        result = self.run_fixture(HTML.replace('href="/example_target/followers/"',
                                               'aria-disabled="true" href="/example_target/followers/"'))
        self.assertEqual(result["error"], "ui_timeout")
        self.assertIn("disabled", result["click_failure_hints"])
        self.assertTrue(result["control_after_failure"]["disabled"])
        self.assertFalse(result["followers"]["dialog_opened"])

    def test_dialog_hints_are_read_only_and_do_not_save_private_text(self):
        context = self.browser.new_context()
        context.route("**/*", lambda route: route.abort())
        try:
            page = context.new_page()
            page.set_content('<a id="control">335 followers</a><div role="dialog" style="position:fixed;inset:0">'
                             'SECRET private text Cookies <button onclick="this.remove()">Decline optional cookies</button></div>')
            result = control_diagnostic(page.locator('#control'))
            self.assertFalse(result['center_hits_control'])
            self.assertTrue(result['hit_inside_dialog'])
            self.assertTrue(result['dialog_hints'][0]['cookie_text'])
            self.assertTrue(result['dialog_hints'][0]['optional_cookie_rejection_control'])
            self.assertEqual(page.get_by_role('button').count(), 1)
            import json
            self.assertNotIn('SECRET', json.dumps(result))
        finally:
            context.close()

    def test_login_hint_is_not_treated_as_cookie_consent(self):
        context = self.browser.new_context()
        context.route("**/*", lambda route: route.abort())
        try:
            page = context.new_page()
            page.set_content('<a id="control">335 followers</a><div role="dialog">Log in to Instagram</div>')
            hints = control_diagnostic(page.locator('#control'))['dialog_hints'][0]
            self.assertTrue(hints['login_text'])
            self.assertFalse(hints['cookie_text'])
            self.assertFalse(hints['optional_cookie_rejection_control'])
        finally:
            context.close()

    def test_optional_invitation_closed_by_button_before_list_click(self):
        invitation = ('<div role="dialog" style="position:fixed;inset:0;z-index:9999">'
                      'Log in to Instagram<button aria-label="Close" onclick="this.parentElement.remove()">X</button></div>')
        result = self.run_fixture(HTML + invitation)
        self.assertIsNone(result['error'])
        self.assertEqual(result['login_prompt_actions'], [{'kind': 'login_invitation',
                         'action': 'official_close_button', 'attempted': True, 'closed': True}])
        self.assertEqual(len(result['followers']['rows']), 10)
        self.assertEqual(len(result['following']['rows']), 10)

    def test_invitation_without_close_stops_before_clicking_background(self):
        result = self.run_fixture(HTML + '<div role="dialog" style="position:fixed;inset:0">Log in to Instagram</div>')
        self.assertEqual(result['error'], 'login_prompt_not_dismissible')
        self.assertEqual(result['failed_operation'], 'inspect_followers_interstitial')
        self.assertEqual(result['login_prompt_actions'], [])
        self.assertIsNone(result['following'])

    def test_explicit_login_requirement_with_close_is_not_dismissed(self):
        result = self.run_fixture(HTML + '<div role="dialog">Log in to continue<button aria-label="Close">X</button></div>')
        self.assertEqual(result['error'], 'authentication_required')
        self.assertEqual(result['login_prompt_actions'], [])

    def test_unknown_modal_is_not_closed(self):
        result = self.run_fixture(HTML + '<div role="dialog">An unknown notice<button aria-label="Close">X</button></div>')
        self.assertEqual(result['error'], 'unrecognized_dialog_present')
        self.assertEqual(result['login_prompt_actions'], [])

    def test_invitation_reappearing_before_following_stops(self):
        invitation = ('<div role="dialog" style="position:fixed;inset:0">Log in to Instagram'
                      '<button aria-label="Close" onclick="this.parentElement.remove()">X</button></div>')
        import json
        html = HTML + invitation + '<script>document.addEventListener("keydown", e => {' \
               'if(e.key === "Escape") document.body.insertAdjacentHTML("beforeend", ' + json.dumps(invitation) + ');});</script>'
        result = self.run_fixture(html)
        self.assertEqual(result['error'], 'login_prompt_reappeared')
        self.assertEqual(len(result['login_prompt_actions']), 1)
        self.assertEqual(result['following']['rows'], [])

    def test_login_invitation_after_list_click_is_not_closed_or_read(self):
        html = HTML.replace("heading.textContent = event.currentTarget.getAttribute('href').includes('/followers/') ? 'Followers' : 'Following';",
                            "heading.textContent = 'Log in to Instagram';")
        result = self.run_fixture(html)
        self.assertEqual(result['error'], 'authentication_required')
        self.assertEqual(result['failed_operation'], 'classify_followers_dialog')
        self.assertTrue(result['followers']['dialog_opened'])
        self.assertFalse(result['followers']['list_context_confirmed'])
        self.assertEqual(result['followers']['rows'], [])
        self.assertIsNone(result['following'])
        self.assertEqual(result['login_prompt_actions'], [])

    def test_unrecognized_dialog_does_not_produce_relationship_records(self):
        html = HTML.replace("heading.textContent = event.currentTarget.getAttribute('href').includes('/followers/') ? 'Followers' : 'Following';",
                            "heading.textContent = 'Suggested accounts';")
        result = self.run_fixture(html)
        self.assertEqual(result['error'], 'list_dialog_unconfirmed')
        self.assertEqual(result['followers']['rows'], [])
        self.assertEqual(result['outcome'], 'interrupted')

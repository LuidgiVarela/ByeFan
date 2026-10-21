import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from backend.pilot.queue import JobQueue
from backend.pilot.site_preview import SitePreviewServer
from backend.pilot.worker import SyntheticCollector, Worker

ROOT = Path(__file__).resolve().parents[1]


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = JobQueue(Path(self.temp.name)/'jobs.sqlite3', ['fixture_0'], cache_seconds=0)
        self.server = SitePreviewServer(('127.0.0.1', 0), self.queue, ROOT/'site/dist')
        self.serving = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05})
        self.serving.start()
        self.stop = threading.Event()
        self.worker = threading.Thread(target=Worker(self.queue, SyntheticCollector(1)).run, args=(self.stop,))
        self.worker.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.stop.set()
        self.worker.join(timeout=10)
        self.server.shutdown()
        self.serving.join()
        self.server.server_close()
        self.temp.cleanup()

    def http(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        try:
            connection.request(method, path, body, headers or {})
            r=connection.getresponse()
            return r.status, r.read(), dict(r.getheaders())
        finally:
            connection.close()

    def test_no_real_target_credentials_files_or_cross_origin_writes(self):
        self.assertEqual(self.http('GET','/')[0],200)
        for path in ('/reports/live-test.json','/../README.md','/.openai/hosting.json','/jobs','/health'):
            self.assertEqual(self.http('GET',path)[0],404)
        self.assertEqual(self.http('POST','/demo/jobs')[0],403)
        self.assertEqual(self.http('POST','/demo/jobs',headers={
            'X-ByeFan-Demo':'1','Origin':'https://external.example'})[0],403)
        self.assertEqual(self.http('POST','/demo/jobs',json.dumps({'username':'example_target'}),{
            'X-ByeFan-Demo':'1'})[0],400)
        status, data, _ = self.http('POST','/demo/jobs',headers={'X-ByeFan-Demo':'1'})
        self.assertEqual(status,202)
        ticket=json.loads(data)
        self.assertEqual(self.http('GET','/demo/jobs/'+ticket['ticket_id'])[0],404)
        status,data,_=self.http('GET','/demo/jobs/'+ticket['ticket_id'],headers={'Authorization':'Bearer '+ticket['access_token']})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(data)['target'],'fixture_0')
        self.assertTrue(json.loads(data)['simulation'])

    def test_local_queue_flow_search_filter_cancel_and_mobile_layout(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':390,'height':844}, reduced_motion='reduce')
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.url+'/') else route.abort())
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                posts=[]
                page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
                page.goto(self.url)
                page.locator('#username').fill('example_target')
                page.locator('#submit-button').click()
                self.assertIn('ainda não',page.locator('#form-error').inner_text())
                self.assertEqual(posts,[])
                page.locator('#use-example').click()
                page.locator('#submit-button').click()
                page.locator('#results-section').wait_for(state='visible',timeout=10000)
                self.assertEqual(len(posts),1)
                self.assertEqual(page.locator('.person').count(),10)
                self.assertEqual(page.locator('[data-filter="unseen"]').get_attribute('aria-pressed'),'true')
                self.assertEqual(page.locator('#people-list .person-name').first.inner_text(),'@exemplo_50')
                self.assertIn('Quem pode não seguir',page.locator('#results-title').inner_text())
                page.locator('[data-filter="unseen"]').click()
                page.locator('#more-button').click()
                self.assertEqual(page.locator('.person').count(),11)
                page.locator('#search').fill('@EXEMPLO_60')
                self.assertEqual(page.locator('.person').count(),1)
                page.locator('#search').fill('<img src=x onerror=alert(1)>')
                self.assertTrue(page.locator('#empty-state').is_visible())
                self.assertEqual(page.locator('#people-list img').count(),0)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                page.locator('#privacy-button').click()
                self.assertTrue(page.locator('#privacy-dialog').is_visible())
                page.keyboard.press('Escape')
                self.assertFalse(page.locator('#privacy-dialog').is_visible())
                page.locator('#reset-button').click()
                page.locator('#submit-button').click()
                page.locator('#cancel-button').click()
                self.assertTrue(page.locator('#progress-section').is_hidden())
                page.wait_for_timeout(1200)
                self.assertTrue(page.locator('#results-section').is_hidden())
                self.assertEqual(errors,[])
            finally:
                browser.close()

    def test_hosted_mode_uses_only_static_assets_and_labels_fiction(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                seen=[]
                def local_asset(route):
                    from urllib.parse import urlparse
                    from backend.pilot.site_preview import ASSETS
                    path=urlparse(route.request.url).path
                    seen.append(path)
                    asset=ASSETS.get(path)
                    if not asset:
                        route.abort();return
                    route.fulfill(body=(ROOT/'site/dist'/asset[0]).read_bytes(),content_type=asset[1])
                context.route('**/*',local_asset)
                page=context.new_page()
                page.goto('https://byefan-preview.example/')
                output=ROOT/'reports/site-preview'
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'desktop.png'),full_page=True)
                page.locator('#use-example').click()
                page.locator('#submit-button').click()
                page.locator('#results-section').wait_for(state='visible',timeout=10000)
                self.assertIn('RESULTADO PARCIAL',page.locator('#results-section').inner_text())
                self.assertFalse(any(path.startswith('/demo') for path in seen))
                page.screenshot(path=str(output/'desktop-results.png'),full_page=True)
                page.locator('#reset-button').click()
                page.set_viewport_size({'width':375,'height':812})
                page.evaluate('scrollTo(0,0)')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                page.screenshot(path=str(output/'mobile.png'),full_page=True)
            finally:
                browser.close()

    def test_saved_analysis_is_same_origin_read_only_and_not_in_static_html(self):
        from backend.services.relationships import analyze_snapshot
        from tests.test_relationships import fixture
        from playwright.sync_api import sync_playwright
        self.server.saved_analysis=analyze_snapshot(fixture())
        self.assertEqual(self.http('GET','/local/analysis')[0],404)
        self.assertEqual(self.http('POST','/local/analysis',headers={'X-ByeFan-Local':'1'})[0],403)
        self.assertEqual(self.http('POST','/local/analysis',headers={
            'X-ByeFan-Local':'1','Origin':'https://external.example'})[0],403)
        self.assertNotIn(b'carol',self.http('GET','/')[1])
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.url+'/') else route.abort())
                page=context.new_page()
                external=[]
                page.on('request',lambda r:external.append(r.url) if not r.url.startswith(self.url+'/') else None)
                page.goto(self.url)
                page.locator('#open-saved').click()
                page.locator('#saved-people .person').wait_for(state='visible')
                self.assertEqual(page.locator('#saved-people .person-name').inner_text(),'@carol')
                self.assertIn('candidatos',page.locator('#saved-content').inner_text())
                self.assertEqual(page.locator('#saved-primary-count').inner_text(),'1')
                self.assertIn('Quem pode não seguir',page.locator('#saved-title').inner_text())
                self.assertIn('Você segue ✓',page.locator('#saved-people').inner_text())
                self.assertIn('Segue você: não encontrado',page.locator('#saved-people').inner_text())
                link=page.locator('#saved-people a')
                self.assertEqual(link.get_attribute('href'),'https://www.instagram.com/carol/')
                self.assertEqual(link.get_attribute('rel'),'noopener noreferrer')
                page.locator('#saved-search').fill('@CAROL')
                self.assertEqual(page.locator('#saved-people .person').count(),1)
                page.locator('#saved-search').fill('')
                page.locator('[data-key="observed_mutual"]').click()
                self.assertEqual(page.locator('#saved-people .person-name').inner_text(),'@bob')
                page.locator('#saved-search').fill('no_match')
                self.assertEqual(page.locator('#saved-people .person').count(),0)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                page.locator('.saved-history summary').click()
                self.assertIn('histórico suficiente',page.locator('.saved-history').inner_text())
                self.assertEqual(external,[])
            finally:
                browser.close()

    def test_verified_labels_and_empty_lists_use_actual_validation(self):
        from backend.services.relationships import analyze_snapshot
        from tests.test_relationships import fixture, reference
        from tests.test_hybrid_collector import list_fixture
        from playwright.sync_api import sync_playwright
        import copy
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.url+'/') else route.abort())
                for empty in (False,True):
                    current=fixture()
                    ref=reference()
                    if empty:
                        current['following']=list_fixture('following',[{'id':'2','username':'bob'}])
                        ref['following_ids']=['2']
                    repeat=copy.deepcopy(current)
                    repeat.update(run_id='repeat',started_at='2026-10-02T12:00:00+00:00')
                    for validated in (False,True):
                        with self.subTest(empty=empty,validated=validated):
                            self.server.saved_analysis=analyze_snapshot(current,
                                repeat=repeat if validated else None,reference=ref if validated else None)
                            page=context.new_page()
                            page.goto(self.url)
                            page.locator('#open-saved').click()
                            page.locator('#saved-primary-count').wait_for()
                            self.assertEqual(page.locator('#saved-primary-count').inner_text(),'0' if empty else '1')
                            self.assertEqual(page.locator('#saved-title').inner_text(),
                                'Quem não segue você de volta.' if validated else 'Quem pode não seguir você de volta?')
                            if empty:
                                self.assertEqual(page.locator('#saved-people .person').count(),0)
                                self.assertEqual(page.locator('#saved-count').inner_text(),'Nenhum perfil nesta categoria.')
                            elif validated:
                                self.assertIn('Não segue você',page.locator('#saved-people').inner_text())
                                self.assertNotIn('não encontrado',page.locator('#saved-people').inner_text())
                            page.close()
            finally:
                browser.close()

    def test_saved_list_paginates_and_searches_beyond_first_page(self):
        from backend.services.relationships import analyze_snapshot
        from tests.test_relationships import fixture
        from tests.test_hybrid_collector import list_fixture
        from playwright.sync_api import sync_playwright
        report=fixture()
        report['following']=list_fixture('following',[
            {'id':str(100+i),'username':f'fixture_{i:02d}'} for i in range(45)])
        self.server.saved_analysis=analyze_snapshot(report)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':375,'height':812},reduced_motion='reduce')
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(self.url+'/') else route.abort())
                page=context.new_page()
                page.goto(self.url)
                page.locator('#open-saved').click()
                page.locator('#saved-primary-count').wait_for()
                self.assertEqual(page.locator('#saved-people .person').count(),20)
                page.locator('#saved-more').click()
                self.assertEqual(page.locator('#saved-people .person').count(),40)
                page.locator('#saved-more').click()
                self.assertEqual(page.locator('#saved-people .person').count(),45)
                self.assertTrue(page.locator('#saved-more').is_hidden())
                page.locator('#saved-search').fill('@FIXTURE_44')
                self.assertEqual(page.locator('#saved-people .person-name').inner_text(),'@fixture_44')
                page.locator('#saved-search').fill('')
                self.assertEqual(page.locator('#saved-people .person').count(),20)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                page.locator('#saved-analysis').screenshot(path=str(ROOT/'reports/site-preview/saved-list-mobile.png'))
            finally:
                browser.close()

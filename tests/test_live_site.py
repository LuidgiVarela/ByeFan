"""Exercise the real-adapter wiring using a fake runner and loopback only."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from backend.pilot.live_collection import LocalCollection
from backend.pilot.queue import JobQueue
from backend.pilot.site_preview import SitePreviewServer
from tests.test_relationships import fixture

ROOT=Path(__file__).resolve().parents[1]


class LiveSiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.release=threading.Event()
        self.calls=0
        def runner(*args,**kwargs):
            self.calls+=1
            report=fixture()
            report['elapsed_seconds']=2
            kwargs['on_progress'](report)
            while not self.release.wait(.01) and not kwargs['cancelled']():
                pass
            return None,None,report
        self.live=LocalCollection(Path(self.temp.name)/'attempts','offline-fixture',runner=runner)
        queue=JobQueue(Path(self.temp.name)/'jobs.sqlite3',['fixture_0'])
        self.server=SitePreviewServer(('127.0.0.1',0),queue,ROOT/'site/dist',live_collection=self.live)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05})
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.live.close()
        self.temp.cleanup()

    def request(self,method,path,headers=None,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        try:
            connection.request(method,path,body,headers or {})
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def test_http_scope_admission_token_and_default_disabled(self):
        path='/local/collection'
        headers={'Origin':self.url,'X-ByeFan-Local':'1'}
        self.assertEqual(self.request('POST',path)[0],403)
        self.assertEqual(self.request('POST',path,{'Origin':'https://evil.example','X-ByeFan-Local':'1'})[0],403)
        self.assertEqual(self.request('POST',path,headers,json.dumps({'username':'someone_else'}))[0],400)
        self.assertEqual(self.calls,0)
        self.server.live_collection=None
        self.assertEqual(self.request('POST',path,headers)[0],403)
        self.server.live_collection=self.live
        status,ticket=self.request('POST',path,headers)
        self.assertEqual(status,202)
        self.assertEqual(self.request('POST',path,headers)[0],409)
        ticketpath=path+'/'+ticket['ticket_id']
        self.assertEqual(self.request('GET',ticketpath)[0],404)
        token={'Authorization':'Bearer '+ticket['access_token']}
        self.assertEqual(self.request('DELETE',ticketpath,token)[0],403)
        self.assertEqual(self.request('GET',ticketpath,token)[0],200)
        self.assertNotIn(ticket['access_token'],json.dumps(self.request('GET','/demo/config')))
        self.assertEqual(self.request('DELETE',ticketpath,{**headers,**token})[0],200)

    def browser_flow(self,cancel=False):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':390,'height':844},reduced_motion='reduce')
                external=[]
                def route_request(route):
                    if route.request.url.startswith(self.url+'/'):
                        route.continue_()
                    else:
                        external.append(route.request.url)
                        route.abort()
                context.route('**/*',route_request)
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.url)
                page.locator('#start-live').wait_for()
                self.assertEqual(self.calls,0) # Loading the page never starts a run.
                page.locator('#start-live').click()
                page.locator('#saved-status').filter(has_text='2 seguidores').wait_for()
                self.assertTrue(page.locator('#start-live').is_disabled())
                if cancel:
                    page.locator('#cancel-live').click()
                    page.locator('#saved-status').filter(has_text='Consulta cancelada').wait_for()
                    self.assertEqual(page.locator('#saved-people').count(),0)
                else:
                    self.release.set()
                    page.locator('#saved-primary-count').wait_for()
                    self.assertEqual(page.locator('#saved-primary-count').inner_text(),'1')
                    self.assertEqual(page.locator('#saved-people .person-name').inner_text(),'@carol')
                    self.assertIn('Quem pode',page.locator('#saved-title').inner_text())
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.locator('#saved-analysis').screenshot(path=str(ROOT/'reports/site-preview/live-pilot-fixture.png'))
                self.assertEqual(self.calls,1)
                page.reload()
                page.locator('#start-live').wait_for()
                self.assertTrue(page.locator('#start-live').is_disabled())
                self.assertEqual(self.calls,1)
                self.assertEqual(external,[])
                self.assertEqual(errors,[])
            finally:
                browser.close()

    def test_local_ui_collect_progress_and_partial_comparison(self):
        self.browser_flow()

    def test_local_ui_cancels_without_showing_result(self):
        self.browser_flow(cancel=True)

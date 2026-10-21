"""Offline virtualized and paginated DOM experiment. All browser network blocked.

Synthetic layout adjusts panel height with the viewport. Instagram may not.
No real-network performance or completeness is inferred from these fixtures.
"""
import json
from pathlib import Path
import time

from backend.experimental.playwright_browser import extract_visible_people, scroll_metrics_and_advance


HTML = '''<style>body{margin:0} #list{overflow-y:auto; overflow-anchor:none; height:calc(100vh - 400px)}
#space{position:relative} a{position:absolute;display:block;height:48px}</style>
<div role="dialog"><div id="list"><div id="space"></div></div></div>
<script>
(() => {
const list=document.querySelector('#list'), space=document.querySelector('#space');
const total=511, rowHeight=48;
let loaded=24, busy=false;
function paint(){
 space.style.height=(loaded*rowHeight)+'px';
 const first=Math.max(0,Math.floor(list.scrollTop/rowHeight)-1);
 const last=Math.min(loaded,Math.ceil((list.scrollTop+list.clientHeight)/rowHeight)+1);
 space.replaceChildren();
 for(let i=first;i<last;i++){
   const a=document.createElement('a'); a.href='/fixture_'+i+'/';
   a.textContent='fixture_'+i; a.dataset.userId=String(i+1);
   a.style.top=(i*rowHeight)+'px'; space.append(a);
 }
}
list.addEventListener('scroll',()=>{
 paint();
 if(!busy && loaded<total && list.scrollTop+list.clientHeight>=loaded*rowHeight-48){
   busy=true; list.setAttribute('aria-busy','true');
   setTimeout(()=>{loaded=Math.min(total,loaded+12);busy=false;
     list.setAttribute('aria-busy','false');paint();},30);
 }
});
paint();
})();
</script>'''


def measure(page, height):
    page.set_viewport_size({'width': 390, 'height': height})
    page.set_content(HTML)
    dialog = page.get_by_role('dialog')
    seen, cycles, peak_mounted, ids = set(), 0, 0, set()
    started = time.perf_counter()
    while cycles <= 400:
        snapshot = extract_visible_people(dialog, include_geometry=True)
        seen.update(row['username'] for row in snapshot['rows'])
        ids.update(row['id'] for row in snapshot['rows'])
        peak_mounted = max(peak_mounted, len(snapshot['rows']))
        if len(seen) == 511:
            break
        scroll_metrics_and_advance(dialog)
        # Flush layout and the synthetic async page load, without network or 1.1s production pause.
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        page.wait_for_function("document.querySelector('#list').getAttribute('aria-busy') !== 'true'")
        cycles += 1
    expected = {f'fixture_{i}' for i in range(511)}
    assert seen == expected and ids == {str(i+1) for i in range(511)}, (
        'fixture_lost_records', height, cycles, len(seen), len(ids),
        sorted(expected - seen)[:10], snapshot['geometry'])
    assert peak_mounted < 511, 'fixture_not_virtualized'
    return {'viewport_height': height, 'container_height': snapshot['geometry']['viewport'],
            'cycles': cycles, 'unique_fixture_usernames': len(seen), 'unique_fixture_ids': len(ids),
            'peak_mounted_rows': peak_mounted, 'fixture_integrity_verified': True,
            'elapsed_without_production_pauses_seconds': round(time.perf_counter()-started, 3)}


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            results = [measure(page, height) for height in (800, 1000, 1200)]
        finally:
            browser.close()
    report = {'mode': 'offline_synthetic_virtualized_dom', 'network': 'blocked',
              'instagram_validated': False, 'production_pause_unchanged_ms': 1100,
              'cases': results}
    output = Path('reports/pilot/viewport-benchmark.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()

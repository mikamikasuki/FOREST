"""Public source reading and optional screenshots, using a fresh browser context."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
import httpx
from .sources import _public_url

class TextPage(HTMLParser):
    def __init__(self): super().__init__(); self.hidden=0; self.parts=[]; self.title=''; self.in_title=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'): self.hidden+=1
        if tag=='title': self.in_title=True
        if tag in ('p','div','section','h1','h2','h3','li','br'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'): self.hidden=max(0,self.hidden-1)
        if tag=='title': self.in_title=False
    def handle_data(self,data):
        if self.in_title: self.title+=data
        if not self.hidden: self.parts.append(data)

def read_page(url,output_dir,screenshot=False):
    initial=url; target=Path(output_dir); target.mkdir(parents=True,exist_ok=True)
    for _ in range(6):
        _public_url(url)
        with httpx.stream('GET',url,follow_redirects=False,timeout=30,headers={'User-Agent':'FOREST-research/1.0'}) as response:
            if response.is_redirect: url=urljoin(url,response.headers['location']); continue
            response.raise_for_status(); data=b''
            for chunk in response.iter_bytes():
                data+=chunk
                if len(data)>5_000_000: raise ValueError('Public page exceeds 5 MB; import the source PDF instead')
            parser=TextPage(); parser.feed(data.decode('utf-8',errors='replace')); text='\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
            (target/'page.txt').write_text(text)
            chunk_size=20000
            passages=[{'id':f'page-{index+1}','page':None,'section':f'Page text, part {index+1}',
                       'text':text[start:start+chunk_size],'source_url':url}
                      for index,start in enumerate(range(0,len(text),chunk_size))]
            result={'url':url,'title':parser.title.strip(),'text':text[:40000],
                    'text_length':len(text),'text_truncated':len(text)>40000,
                    'passages':passages,'text_path':str(target/'page.txt'),
                    'reading_scope':'public_page_text','trusted_instructions':False}
            if screenshot:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(channel='chrome',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    def restrict(route):
                        try:
                            if route.request.url.startswith(('data:','blob:')): route.continue_(); return
                            _public_url(route.request.url); route.continue_()
                        except Exception: route.abort()
                    context.route('**/*',restrict); page=context.new_page(); page.goto(url,wait_until='domcontentloaded',timeout=30000); page.screenshot(path=str(target/'page.png'),full_page=True); browser.close()
                result['screenshot_path']=str(target/'page.png')
            return result
    raise ValueError('Too many public-page redirects')

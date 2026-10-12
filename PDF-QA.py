#!/usr/bin/env python3
"""Offline Linux PDF identity checker; requires Poppler pdftotext."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

LABELS = r'(?:ECN|ECM|Part\s*(?:Number|No\.?|#)|P/N|Document\s+Title|Title|Revision|Rev\.?|Version)'
PATTERN = re.compile(r'\b('+LABELS+r')\s*[:=]\s*(.*?)(?=\s+\b'+LABELS+r'\s*[:=]|$)', re.I)

def normalize(s):
    return ' '.join(unicodedata.normalize('NFKC',s).split()).casefold()

def field_name(label):
    label=label.casefold()
    if label in ('ecn','ecm'): return 'ecn'
    if label.startswith('part') or label=='p/n': return 'part'
    if 'title' in label: return 'title'
    return 'revision'

def extract(path, full=False):
    if not shutil.which('pdftotext'): raise ValueError('Install Poppler: sudo apt install poppler-utils')
    if Path(path).suffix.casefold()!='.pdf': raise ValueError('Choose a PDF file.')
    proc=subprocess.run(['pdftotext','-bbox-layout','-enc','UTF-8',str(Path(path).resolve()),'-'],capture_output=True,timeout=90)
    if proc.returncode: raise ValueError('PDF could not be read: '+proc.stderr.decode('utf-8','replace').strip())
    root=ET.fromstring(proc.stdout)
    pages=[]
    for page in root.iter():
        if page.tag.split('}')[-1]!='page': continue
        height=float(page.get('height')); lines=[]; all_lines=[]
        for line in page.iter():
            if line.tag.split('}')[-1]!='line': continue
            words=[w.text or '' for w in line if w.tag.split('}')[-1]=='word']
            text=' '.join(words); all_lines.append(text)
            # Cover page includes all text; following pages use outer 25% bands.
            if full or not pages or float(line.get('yMin'))<height*.25 or float(line.get('yMax'))>height*.75:
                lines.append(text)
        pages.append({'lines':lines,'readable':bool(''.join(all_lines).strip())})
    if not pages: raise ValueError('No PDF pages found.')
    return pages

def evaluate(pages, expected, every_page=True):
    findings=[]; counts={k:0 for k in expected}
    for number,page in enumerate(pages,1):
        if not page['readable']:
            findings.append({'page':number,'field':'text','status':'FAIL','detail':'No extractable text. Scan/image or blank page: cannot verify.'})
            continue
        found={k:[] for k in expected}
        for line in page['lines']:
            for label,value in PATTERN.findall(line):
                found[field_name(label)].append(value.strip())
        # Unlabeled titles are common. Accept exact expected title as a whole
        # normalized line or contiguous sequence of lines, not a substring.
        if not found['title']:
            lines=page['lines']
            for i in range(len(lines)):
                if any(normalize(' '.join(lines[i:i+n]))==normalize(expected['title']) for n in range(1,5)):
                    found['title'].append(expected['title']); break
        for key,wanted in expected.items():
            values=found[key]; counts[key]+=len(values)
            if not values and every_page:
                findings.append({'page':number,'field':key,'status':'FAIL','detail':'Missing or unrecognized identification field.'})
            for value in values:
                ok=normalize(value)==normalize(wanted)
                findings.append({'page':number,'field':key,'status':'PASS' if ok else 'FAIL','detail':f'Found: {value} | Expected: {wanted}'})
    for key,count in counts.items():
        if not count:
            findings.append({'page':None,'field':key,'status':'FAIL','detail':'No recognized field anywhere in selected scope.'})
    return findings

def check(path,expected,every_page=True,full=False):
    if set(expected)!={'ecn','part','title','revision'} or any(not str(v).strip() for v in expected.values()):
        raise ValueError('Enter ECN, part number, title and revision.')
    source=Path(path).read_bytes()
    findings=evaluate(extract(path,full),expected,every_page)
    if source!=Path(path).read_bytes(): raise ValueError('PDF changed during checking. Please run again.')
    return {'tool':'aicram PDF QA 1.0','file':str(Path(path).resolve()),
        'sha256':hashlib.sha256(source).hexdigest(),'checked_at':datetime.now(timezone.utc).isoformat(),
        'expected':expected,'require_each_page':every_page,
        'scope':'All page text' if full else 'Full cover page; top/bottom 25% of later pages',
        'result':'FAIL' if any(f['status']=='FAIL' for f in findings) else 'PASS',
        'findings':findings}

def report_text(r):
    return f"{r['result']} — configured identity checks\nScope: {r['scope']}\n\n"+'\n'.join(f"{f['status']} | Page {f['page'] or 'document'} | {f['field']} | {f['detail']}" for f in r['findings'])

def save_report(path,r):
    if Path(path).resolve()==Path(r['file']).resolve(): raise ValueError('Report cannot overwrite the input PDF.')
    content=json.dumps(r,indent=2,ensure_ascii=False) if Path(path).suffix.lower()=='.json' else report_text(r)
    Path(path).write_text(content,encoding='utf-8')

def gui():
    import tkinter as tk
    from tkinter import ttk,filedialog,messagebox,scrolledtext
    from concurrent.futures import ThreadPoolExecutor
    app=tk.Tk(); app.title('aicram — PDF QA'); app.geometry('900x660')
    frame=ttk.Frame(app,padding=16); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='PDF document QA',font=('',18,'bold')).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
    values={}
    for row,(key,label) in enumerate([('file','PDF file'),('ecn','ECN / control number'),('part','Part number'),('title','Document title'),('revision','Revision')],1):
        ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',pady=6,padx=(0,15))
        values[key]=tk.StringVar(); ttk.Entry(frame,textvariable=values[key],width=65).grid(row=row,column=1,sticky='ew')
    def browse():
        p=filedialog.askopenfilename(filetypes=[('PDF documents','*.pdf')])
        if p: values['file'].set(p)
    ttk.Button(frame,text='Browse',command=browse).grid(row=1,column=2,padx=10)
    each=tk.BooleanVar(value=True); full=tk.BooleanVar(value=False)
    ttk.Checkbutton(frame,text='Require all four fields on every page',variable=each).grid(row=6,column=1,sticky='w',pady=6)
    ttk.Checkbutton(frame,text='Search entire pages (include body text)',variable=full).grid(row=7,column=1,sticky='w')
    ttk.Label(frame,text='Default: whole cover; top/bottom quarter of later pages. PDF stays unchanged.').grid(row=8,column=0,columnspan=3,sticky='w',pady=10)
    output=scrolledtext.ScrolledText(frame,wrap='word',height=15); output.grid(row=10,column=0,columnspan=3,sticky='nsew',pady=10)
    state={}; pool=ThreadPoolExecutor(max_workers=1)
    def run():
        state.clear(); save.config(state='disabled'); button.config(state='disabled')
        output.delete('1.0','end'); output.insert('end','Checking PDF…')
        future=pool.submit(check,values['file'].get(),{k:values[k].get() for k in ('ecn','part','title','revision')},each.get(),full.get())
        def poll():
            if not future.done(): app.after(100,poll); return
            button.config(state='normal'); output.delete('1.0','end')
            try:
                r=future.result(); state['report']=r; output.insert('end',report_text(r)); save.config(state='normal')
            except Exception as exc: output.insert('end','ERROR: '+str(exc))
        app.after(100,poll)
    def save_action():
        p=filedialog.asksaveasfilename(defaultextension='.txt',initialfile='pdf-qa-report.txt',filetypes=[('Text report','*.txt'),('JSON report','*.json')])
        if p:
            try: save_report(p,state['report'])
            except Exception as exc: messagebox.showerror('Unable to save',str(exc))
    button=ttk.Button(frame,text='Check PDF',command=run); button.grid(row=9,column=1,sticky='w')
    save=ttk.Button(frame,text='Save report',command=save_action,state='disabled'); save.grid(row=9,column=2)
    frame.columnconfigure(1,weight=1); frame.rowconfigure(10,weight=1)
    app.mainloop(); pool.shutdown(wait=False)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('file',nargs='?')
    for key in ('ecn','part','title','revision','report'): p.add_argument('--'+key)
    p.add_argument('--allow-missing-pages',action='store_true'); p.add_argument('--full-page',action='store_true')
    a=p.parse_args()
    if not a.file: gui(); return 0
    try:
        r=check(a.file,{k:getattr(a,k) or '' for k in ('ecn','part','title','revision')},not a.allow_missing_pages,a.full_page)
        if a.report: save_report(a.report,r)
        print(report_text(r)); return 0 if r['result']=='PASS' else 1
    except Exception as exc: print('ERROR: '+str(exc),file=sys.stderr); return 2

if __name__=='__main__': sys.exit(main())

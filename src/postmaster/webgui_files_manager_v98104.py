"""Presentation-only modern file manager styles and behaviors.

The existing authenticated preview/download/upload/delete routes remain the
single source of truth; this module never reads or sends file bytes.
"""

STYLE = r'''
/* v9.8.10 file manager — scoped to the Files panel */
#panel-files .pm-drive{min-width:0;position:relative;max-width:100%;color:var(--text)}
#panel-files .pm-drive button,#panel-files .pm-drive select,#panel-files .pm-drive input{font:inherit}
#panel-files .pm-drive button{cursor:pointer}
#panel-files .pm-drive-head{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;padding:22px 22px 18px;background:var(--card);border:1px solid var(--line);border-radius:12px 12px 0 0}
#panel-files .pm-drive-kicker{color:var(--muted);font-size:10px;font-weight:750;letter-spacing:.1em;text-transform:uppercase;margin-bottom:6px}
#panel-files .pm-drive-head h2{font-size:24px;letter-spacing:-.03em;margin:0 0 5px;color:var(--text)}
#panel-files .pm-drive-summary{font-size:12px;color:var(--muted);line-height:1.6}
#panel-files .pm-drive-main{background:var(--card);border:1px solid var(--line);border-top:0;padding:16px 20px 20px;border-radius:0 0 12px 12px;min-width:0}
#panel-files .pm-drive-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:9px;margin-bottom:18px}
#panel-files .pm-drive-search{flex:1 1 260px;min-width:180px;position:relative}
#panel-files .pm-drive-search span{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none;font-size:17px}
#panel-files .pm-drive-search input{width:100%;padding:10px 12px 10px 36px;border:1px solid var(--line);border-radius:9px;background:var(--card2);color:var(--text);box-sizing:border-box}
#panel-files .pm-drive-toolbar select{width:auto;max-width:240px;min-width:155px;min-height:40px;border:1px solid var(--line);border-radius:9px;background:var(--card2);color:var(--text);padding:8px 11px}
#panel-files .pm-drive-layout{background:var(--card2);color:var(--text);border:1px solid var(--line);border-radius:9px;padding:9px 13px;min-height:40px;white-space:nowrap}
#panel-files .pm-drive-primary{background:var(--accent);border:1px solid var(--accent);color:var(--bg);font-weight:750;padding:10px 16px;border-radius:9px;display:inline-flex;align-items:center;gap:8px;min-height:40px;cursor:pointer}
#panel-files .pm-drive-primary:hover{filter:brightness(1.08)}
#panel-files .pm-drive-inventory-head{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:7px;margin:0 1px 12px}
#panel-files .pm-drive-inventory-head strong{font-size:14px}
#panel-files .pm-drive-inventory-head span{font-size:11px;color:var(--muted)}
#panel-files .pm-drive-table-wrap{width:100%;border:1px solid var(--line);border-radius:10px;overflow-x:auto;background:var(--card)}
#panel-files .pm-drive-table{width:100%;border-collapse:collapse;min-width:660px;table-layout:fixed}
#panel-files .pm-drive-table th{text-align:left;background:var(--card2);font-size:10px;font-weight:750;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);padding:12px 14px}
#panel-files .pm-drive-table th:nth-child(1){width:45%}
#panel-files .pm-drive-table th:nth-child(2){width:24%}
#panel-files .pm-drive-table th:nth-child(3){width:12%}
#panel-files .pm-drive-table th:nth-child(4){width:19%;text-align:right}
#panel-files .pm-drive-table td{padding:10px 14px;border-top:1px solid var(--line);vertical-align:middle}
#panel-files .pm-drive-table tbody tr{transition:background .12s}
#panel-files .pm-drive-table tbody tr:hover{background:color-mix(in srgb,var(--accent) 6%,var(--card))}
#panel-files .pm-drive-file-name{display:flex;align-items:center;gap:11px;min-width:0;max-width:100%;text-decoration:none;color:var(--text)}
#panel-files .pm-drive-file-name:hover .pm-drive-title{text-decoration:underline;text-decoration-color:var(--accent)}
#panel-files .pm-drive-file-icon{width:36px;height:42px;display:grid;place-items:center;flex-shrink:0;border-radius:8px;background:color-mix(in srgb,var(--accent) 12%,var(--card2));border:1px solid color-mix(in srgb,var(--accent) 25%,var(--line));color:var(--accent);font-size:10px;font-weight:800;letter-spacing:.035em}
#panel-files .pm-drive-file-icon.pm-drive-pdf{color:#e6a1a2;background:color-mix(in srgb,#c75458 15%,var(--card));border-color:color-mix(in srgb,#c75458 36%,var(--line))}
#panel-files .pm-drive-file-icon.pm-drive-image{color:#80c9ad;background:color-mix(in srgb,#268668 18%,var(--card))}
#panel-files .pm-drive-file-icon.pm-drive-text{color:#8fcde8;background:color-mix(in srgb,#2686b5 14%,var(--card))}
#panel-files .pm-drive-file-title-wrap{display:block;min-width:0}
#panel-files .pm-drive-title{display:block;font-size:12px;line-height:1.45;font-weight:750;color:var(--text);overflow-wrap:anywhere}
#panel-files .pm-drive-file-sub{display:block;font-size:10px;color:var(--muted);line-height:1.55;margin-top:3px;overflow-wrap:anywhere}
#panel-files .pm-drive-project{font-size:11px;line-height:1.4}
#panel-files .pm-drive-project .project-scope{display:inline-flex;white-space:normal;text-align:left;max-width:100%;overflow-wrap:anywhere}
#panel-files .pm-drive-size{font-size:12px;white-space:nowrap;color:var(--muted)}
#panel-files .pm-drive-actions{display:flex;align-items:center;justify-content:flex-end;gap:6px;min-width:0}
#panel-files .pm-drive-download{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:7px 11px;font-size:11px;font-weight:750;text-decoration:none;border-radius:8px;border:1px solid color-mix(in srgb,var(--accent) 37%,var(--line));color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,var(--card));white-space:nowrap}
#panel-files .pm-drive-download:hover{background:color-mix(in srgb,var(--accent) 18%,var(--card))}
#panel-files .pm-drive-menu{position:relative;flex-shrink:0}
#panel-files .pm-drive-menu summary{list-style:none;cursor:pointer;display:flex;align-items:center;justify-content:center;min-height:32px;min-width:33px;border:1px solid var(--line);border-radius:8px;color:var(--text);background:var(--card2);font-weight:750;font-size:18px}
#panel-files .pm-drive-menu summary::-webkit-details-marker{display:none}
#panel-files .pm-drive-menu[open]{z-index:5}
#panel-files .pm-drive-menu-inner{display:flex;flex-direction:column;gap:4px;position:absolute;right:0;top:calc(100% + 4px);min-width:172px;padding:6px;border:1px solid var(--line);background:var(--card2);border-radius:9px;box-shadow:0 12px 24px #0005;z-index:25}
#panel-files .pm-drive-menu-inner a,#panel-files .pm-drive-menu-inner button{display:block;width:100%;min-height:34px;text-decoration:none;text-align:left;color:var(--text);font-weight:600;background:transparent;border:0;border-radius:6px;padding:8px;white-space:nowrap}
#panel-files .pm-drive-menu-inner a:hover,#panel-files .pm-drive-menu-inner button:hover{background:color-mix(in srgb,var(--accent) 12%,var(--card2))}
#panel-files .pm-drive-menu-inner .pm-drive-delete{color:var(--danger)}
#panel-files .pm-drive-menu-inner form{display:block;margin:0}
#panel-files .pm-drive-table-wrap:has(.pm-drive-menu[open]){overflow:visible}
#panel-files .pm-drive-empty{padding:35px 16px;text-align:center;font-size:12px;color:var(--muted)}
#panel-files .pm-drive-foot{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-top:13px;font-size:11px;color:var(--muted)}
#panel-files .pm-drive-legend{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
#panel-files .pm-drive-head [data-pm-open-upload]{flex:0 0 auto}
#panel-files .pm-drive-upload-shade,#panel-files .pm-drive-preview-shade{position:fixed;inset:0;background:#080f1980;z-index:1150}
#panel-files .pm-drive-upload-pane,#panel-files .pm-drive-preview-pane{position:fixed;top:0;right:0;height:100vh;height:100dvh;width:min(460px,100vw);overflow-y:auto;background:var(--card);border-left:1px solid var(--line);box-shadow:-12px 0 40px #0006;padding:23px;z-index:1160;color:var(--text)}
#panel-files .pm-drive-preview-pane{width:min(660px,100vw)}
#panel-files .pm-drive-upload-pane header,#panel-files .pm-drive-preview-pane header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
#panel-files .pm-drive-upload-pane h3,#panel-files .pm-drive-preview-pane h3{margin:0;font-size:19px}
#panel-files .pm-drive-close{border:1px solid var(--line);background:var(--card2);color:var(--text);border-radius:8px;min-width:35px;min-height:35px;padding:6px 10px}
#panel-files .pm-drive-drop{border:1.5px dashed color-mix(in srgb,var(--accent) 56%,var(--line));background:color-mix(in srgb,var(--accent) 7%,var(--card));border-radius:11px;padding:22px 14px;margin:15px 0;text-align:center}
#panel-files .pm-drive-drop.pm-drive-dragover{background:color-mix(in srgb,var(--accent) 16%,var(--card));border-style:solid}
#panel-files .pm-drive-drop-title{font-weight:750;font-size:13px;margin-bottom:6px}
#panel-files .pm-drive-drop input{display:block;width:100%;min-width:0;max-width:100%;margin-top:12px;font-size:12px}
#panel-files .pm-drive-field{display:block;margin-bottom:13px}
#panel-files .pm-drive-field label{display:block;margin-bottom:6px;font-size:11px;color:var(--muted);font-weight:650}
#panel-files .pm-drive-field select,#panel-files .pm-drive-field input{box-sizing:border-box;width:100%;max-width:100%;min-width:0;min-height:37px;padding:8px 10px;border:1px solid var(--line);background:var(--card2);color:var(--text);border-radius:8px}
#panel-files .pm-drive-upload-submit{width:100%;margin-top:10px;justify-content:center}
#panel-files .pm-drive-preview-name{font-size:14px;font-weight:800;overflow-wrap:anywhere;margin:0 0 4px}
#panel-files .pm-drive-preview-description{font-size:11px;color:var(--muted);overflow-wrap:anywhere;margin:0 0 14px;max-height:5em;overflow-y:auto}
#panel-files .pm-drive-preview-stage{display:flex;align-items:center;justify-content:center;min-height:285px;background:var(--card2);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin:14px 0}
#panel-files .pm-drive-preview-frame{width:100%;height:clamp(340px,63vh,740px);border:0;display:block;background:#fff}
#panel-files .pm-drive-preview-text{display:block;box-sizing:border-box;width:100%;height:clamp(280px,53vh,650px);overflow:auto;padding:16px;margin:0;background:var(--card2);color:var(--text);font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:2;user-select:text}
#panel-files .pm-drive-preview-image{max-width:100%;max-height:65vh;object-fit:contain;margin:auto}
#panel-files .pm-drive-preview-video{max-width:100%;max-height:60vh;width:100%;display:block}
#panel-files .pm-drive-preview-audio{width:95%;max-width:100%;margin:20px auto}
#panel-files .pm-drive-preview-loading{padding:30px;text-align:center;font-size:12px;color:var(--muted)}
#panel-files .pm-drive-preview-pane .pm-drive-preview-stage:has(.pm-drive-preview-text:not([hidden])){background:var(--card2)}
#panel-files .pm-drive-preview-pane .pm-drive-preview-stage:has(.pm-drive-preview-frame:not([hidden])){background:#fff}
#panel-files .pm-drive-preview-unavailable{padding:24px;text-align:center;color:var(--muted);font-size:12px}
#panel-files .pm-drive-preview-meta{display:grid;gap:7px;margin-bottom:18px}
#panel-files .pm-drive-meta-row{display:grid;grid-template-columns:96px minmax(0,1fr);gap:7px;font-size:11px;padding:8px 0;border-bottom:1px solid var(--line)}
#panel-files .pm-drive-meta-row dt{color:var(--muted)}
#panel-files .pm-drive-meta-row dd{margin:0;overflow-wrap:anywhere}
#panel-files .pm-drive-preview-actions{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
#panel-files .pm-drive-preview-actions .pm-drive-download{font-size:12px;min-height:39px}
#panel-files .pm-drive-preview-actions a:not(.pm-drive-download){font-size:11px;color:var(--accent)}
#panel-files .pm-drive-status{min-height:0;font-size:11px;color:var(--muted)}
#panel-files .pm-drive-root [hidden]{display:none!important}
#panel-files .pm-drive-table-wrap.pm-drive-grid-view{overflow:visible;border:0}
#panel-files .pm-drive-grid-view .pm-drive-table,
#panel-files .pm-drive-grid-view .pm-drive-table tbody{display:block;min-width:0}
#panel-files .pm-drive-grid-view .pm-drive-table thead{display:none}
#panel-files .pm-drive-grid-view .pm-drive-table tbody{display:grid;grid-template-columns:repeat(auto-fill,minmax(225px,1fr));gap:11px}
#panel-files .pm-drive-grid-view .pm-drive-table tr{display:flex;flex-direction:column;background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0}
#panel-files .pm-drive-grid-view .pm-drive-table td{display:block!important;border:0;padding:6px;min-width:0;text-align:left}
#panel-files .pm-drive-grid-view .pm-drive-table td.pm-drive-cell-actions{margin-top:auto}
#panel-files .pm-drive-grid-view .pm-drive-actions{justify-content:flex-start}
#panel-files .pm-drive-grid-view .pm-drive-table td.pm-drive-cell-size{font-size:11px}
#panel-files .pm-drive-file-name:focus-visible,
#panel-files .pm-drive a:focus-visible,
#panel-files .pm-drive button:focus-visible,
#panel-files .pm-drive input:focus-visible,
#panel-files .pm-drive select:focus-visible,
#panel-files .pm-drive summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(max-width:820px){
 #panel-files .pm-drive-head{padding:16px}
 #panel-files .pm-drive-main{padding:12px}
 #panel-files .pm-drive-table{min-width:540px}
 #panel-files .pm-drive-table th:nth-child(2),#panel-files .pm-drive-table td.pm-drive-cell-project{display:none}
 #panel-files .pm-drive-table th:nth-child(1){width:54%}
 #panel-files .pm-drive-table th:nth-child(3){width:16%}
 #panel-files .pm-drive-table th:nth-child(4){width:30%}
 #panel-files .pm-drive-grid-view .pm-drive-table td.pm-drive-cell-project{display:block}
}
@media(max-width:520px){
 #panel-files .pm-drive-toolbar select{flex:1 1 130px;min-width:120px;max-width:100%}
 #panel-files .pm-drive-table{min-width:430px}
 #panel-files .pm-drive-table td,#panel-files .pm-drive-table th{padding:10px 8px}
 #panel-files .pm-drive-download{padding:7px}
 #panel-files .pm-drive-upload-pane,#panel-files .pm-drive-preview-pane{padding:16px}
}
'''

SCRIPT = r'''
<script id="pm-drive-v98104-script">
(() => {
  "use strict";
  const qs=(root,selector)=>root ? root.querySelector(selector) : null;
  const rows=root=>Array.from(root.querySelectorAll('tr[data-pm-file-id]'));
  const roots=()=>document.querySelectorAll('#panel-files .pm-drive-root');

  function visibleRows(root){
    const query=(qs(root,'[data-pm-search]')?.value||'').trim().toLocaleLowerCase();
    const project=qs(root,'[data-pm-project-filter]')?.value||'all';
    const items=rows(root);
    let shown=0;
    for(const row of items){
      const searchable=(row.dataset.pmSearch||'').toLocaleLowerCase();
      const match=searchable.includes(query)&&(project==='all'||row.dataset.pmProject===project);
      row.hidden=!match;
      if(match) shown+=1;
    }
    const summary=qs(root,'[data-pm-visible-count]');
    if(summary)summary.textContent=shown+' shown of '+items.length+' loaded';
    const empty=qs(root,'[data-pm-empty]');
    if(empty)empty.hidden=shown!==0;
    const order=qs(root,'[data-pm-sort]')?.value||'recent';
    const tbody=qs(root,'[data-pm-list]');
    if(tbody&&items.length){
      items.sort((a,b)=>{
        if(order==='name')return (a.dataset.pmName||'').localeCompare(b.dataset.pmName||'');
        if(order==='size')return Number(b.dataset.pmSize||0)-Number(a.dataset.pmSize||0);
        return (b.dataset.pmCreated||'').localeCompare(a.dataset.pmCreated||'');
      }).forEach(item=>tbody.appendChild(item));
    }
  }
  function setUpload(root,open){
    const shade=qs(root,'[data-pm-upload-shade]');
    const pane=qs(root,'[data-pm-upload-pane]');
    const button=qs(root,'[data-pm-open-upload]');
    if(!shade||!pane||!button)return;
    shade.hidden=!open;
    pane.hidden=!open;
    button.setAttribute('aria-expanded',String(open));
    root._pmUploadOpen=open;
    if(open){
      qs(pane,'[data-pm-close-upload]')?.focus();
    }else if(button.isConnected){
      button.focus();
    }
  }
  function clearPreviewMedia(root){
    const frame=qs(root,'[data-pm-preview-frame]');
    if(frame)frame.removeAttribute('src');
    const image=qs(root,'[data-pm-preview-image]');
    if(image){
      image.onload=null;
      image.onerror=null;
      image.removeAttribute('src');
    }
    for(const kind of ['audio','video']){
      const media=qs(root,'[data-pm-preview-'+kind+']');
      if(media){
        media.pause();
        media.removeAttribute('src');
        media.load();
      }
    }
    const controller=root._pmPreviewAbort;
    if(controller)controller.abort();
    root._pmPreviewAbort=null;
    root._pmPreviewSeq=(root._pmPreviewSeq||0)+1;
    const text=qs(root,'[data-pm-preview-text]');
    if(text)text.textContent='';
    for(const element of root.querySelectorAll('[data-pm-preview-frame],[data-pm-preview-image],[data-pm-preview-text],[data-pm-preview-audio],[data-pm-preview-video],[data-pm-preview-loading],[data-pm-preview-unavailable]')){
      element.hidden=true;
    }
  }
  function closePreview(root){
    clearPreviewMedia(root);
    const pane=qs(root,'[data-pm-preview-pane]');
    const shade=qs(root,'[data-pm-preview-shade]');
    if(pane)pane.hidden=true;
    if(shade)shade.hidden=true;
    root._pmPreviewOpen=false;
    const opener=root._pmPreviewOpener;
    if(opener&&opener.isConnected)opener.focus();
  }
  function showPreviewError(root,message){
    for(const element of root.querySelectorAll('[data-pm-preview-frame],[data-pm-preview-image],[data-pm-preview-text],[data-pm-preview-audio],[data-pm-preview-video],[data-pm-preview-loading]')){
      element.hidden=true;
    }
    const messageBox=qs(root,'[data-pm-preview-unavailable]');
    if(messageBox){
      messageBox.hidden=false;
      messageBox.textContent=message;
    }
  }
  function openPreview(root,row,opener){
    const pane=qs(root,'[data-pm-preview-pane]');
    const shade=qs(root,'[data-pm-preview-shade]');
    if(!pane||!shade)return false;
    clearPreviewMedia(root);
    const seq=root._pmPreviewSeq;
    const name=row.dataset.pmName||'File';
    const media=(row.dataset.pmMedia||'application/octet-stream').split(';')[0].trim().toLowerCase();
    const supported=row.dataset.pmPreviewSupported==='1';
    const path=row.dataset.pmPreview||'';
    const url=path.startsWith('/dashboard/files/')&&path.endsWith('/preview')?path:'';
    const download=row.dataset.pmDownload||'';
    const setText=(selector,value)=>{
      const element=qs(pane,selector);
      if(element)element.textContent=value||'—';
    };
    setText('[data-pm-detail-name]',name);
    setText('[data-pm-detail-project]',row.dataset.pmProjectLabel||'Global');
    setText('[data-pm-detail-size]',row.dataset.pmSizeLabel||'—');
    setText('[data-pm-detail-kind]',row.dataset.pmMedia||'application/octet-stream');
    setText('[data-pm-detail-id]',row.dataset.pmFileId||'—');
    setText('[data-pm-detail-description]',row.dataset.pmDescription||'');
    setText('[data-pm-detail-tags]',row.dataset.pmTags||'—');
    const newtab=qs(pane,'[data-pm-open-tab]');
    if(newtab){
      newtab.hidden=!(supported&&url);
      if(supported&&url)newtab.href=url;else newtab.removeAttribute('href');
    }
    const directDownload=qs(pane,'[data-pm-direct-download]');
    if(directDownload){
      if(download.startsWith('/dashboard/files/')&&download.endsWith('/download'))
        directDownload.href=download;
      else directDownload.removeAttribute('href');
    }
    root._pmPreviewOpener=opener;
    shade.hidden=false;
    pane.hidden=false;
    root._pmPreviewOpen=true;
    qs(pane,'[data-pm-close-preview]')?.focus();
    if(!supported||!url){
      showPreviewError(root,'Preview is not available for this file format. You can still download the original file.');
      return true;
    }

    if(media==='application/pdf'){
      // Chrome's built-in PDF viewer is itself sandboxed. A sandbox on the
      // embedding iframe blocks the viewer even when it is a safe PDF.
      const frame=qs(pane,'[data-pm-preview-frame]');
      if(frame){frame.hidden=false;frame.src=url;}
      return true;
    }
    if(media.startsWith('image/')){
      const image=qs(pane,'[data-pm-preview-image]');
      if(image){
        image.hidden=false;
        image.alt='Preview of '+name;
        image.onerror=()=>showPreviewError(root,'The image could not be displayed. Use Download to inspect the original.');
        image.src=url;
      }
      return true;
    }
    if(media.startsWith('audio/')||media.startsWith('video/')){
      const kind=media.startsWith('audio/')?'audio':'video';
      const element=qs(pane,'[data-pm-preview-'+kind+']');
      if(element){
        element.hidden=false;
        element.src=url;
        element.load();
        element.onerror=()=>showPreviewError(root,'Media playback is unavailable in this browser. Download is still available.');
      }
      return true;
    }

    // Render source/text/JSON/DOCX as inert DOM text instead of an embedded
    // white browser document. Never use innerHTML or execute source text.
    const waiting=qs(pane,'[data-pm-preview-loading]');
    const source=qs(pane,'[data-pm-preview-text]');
    if(waiting)waiting.hidden=false;
    const controller=new AbortController();
    root._pmPreviewAbort=controller;
    fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal})
      .then(response=>{
        if(!response.ok)throw new Error('HTTP '+response.status);
        return response.text();
      })
      .then(raw=>{
        if(root._pmPreviewSeq!==seq||!root._pmPreviewOpen)return;
        let formatted=raw;
        if(media==='application/json'||name.toLowerCase().endsWith('.json')){
          try{formatted=JSON.stringify(JSON.parse(raw),null,2);}catch(_ignore){}
        }
        if(waiting)waiting.hidden=true;
        if(source){
          source.textContent=formatted||'(Empty file)';
          source.hidden=false;
        }
      })
      .catch(error=>{
        if(error?.name==='AbortError'||root._pmPreviewSeq!==seq)return;
        showPreviewError(root,'Could not load a safe text preview. The original file is available with Download.');
      });
    return true;
  }
  function closeMenus(root,except){
    root.querySelectorAll('.pm-drive-menu[open]').forEach(menu=>{
      if(menu!==except)menu.open=false;
    });
  }
  document.addEventListener('click',event=>{
    const root=event.target.closest('#panel-files .pm-drive-root');
    if(!root)return;
    if(event.target.closest('[data-pm-open-upload]')){
      setUpload(root,true);
      return;
    }
    if(event.target.closest('[data-pm-upload-shade],[data-pm-close-upload]')){
      setUpload(root,false);
      return;
    }
    if(event.target.closest('[data-pm-preview-shade],[data-pm-close-preview]')){
      closePreview(root);
      return;
    }
    const copy=event.target.closest('[data-pm-copy-id]');
    if(copy){
      const value=copy.dataset.pmCopyId||'';
      const status=qs(root,'[data-pm-status]');
      if(navigator.clipboard?.writeText){
        navigator.clipboard.writeText(value).then(()=>{
          if(status)status.textContent='File ID copied';
        }).catch(()=>{
          if(status)status.textContent='File ID: '+value;
        });
      }else if(status)status.textContent='File ID: '+value;
      closeMenus(root);
      return;
    }
    const layout=event.target.closest('[data-pm-layout-toggle]');
    if(layout){
      const wrap=qs(root,'[data-pm-table-wrap]');
      const grid=wrap?.classList.toggle('pm-drive-grid-view')||false;
      layout.setAttribute('aria-pressed',String(grid));
      layout.textContent=grid?'☷ List':'▦ Grid';
      return;
    }
    const previewLink=event.target.closest('[data-pm-preview-link]');
    if(previewLink){
      if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
      const row=previewLink.closest('tr[data-pm-file-id]');
      if(row&&openPreview(root,row,previewLink))event.preventDefault();
      return;
    }
    const row=event.target.closest('tr[data-pm-file-id]');
    if(row&&!event.target.closest('a,button,form,input,select,textarea,summary,details')){
      openPreview(root,row,row);
      return;
    }
    if(!event.target.closest('.pm-drive-menu'))closeMenus(root,null);
  });
  document.addEventListener('keydown',event=>{
    const root=event.target.closest('#panel-files .pm-drive-root');
    if(!root)return;
    if(event.key==='Escape'){
      if(root._pmPreviewOpen)closePreview(root);
      if(root._pmUploadOpen)setUpload(root,false);
      closeMenus(root,null);
      return;
    }
    const row=event.target.closest('tr[data-pm-file-id]');
    if(row&&(event.key==='Enter'||event.key===' ')&&event.target===row){
      event.preventDefault();
      openPreview(root,row,row);
    }
  });
  document.addEventListener('input',event=>{
    const root=event.target.closest('#panel-files .pm-drive-root');
    if(root&&event.target.matches('[data-pm-search]'))visibleRows(root);
  });
  document.addEventListener('change',event=>{
    const root=event.target.closest('#panel-files .pm-drive-root');
    if(!root)return;
    if(event.target.matches('[data-pm-project-filter],[data-pm-sort]'))visibleRows(root);
  });
  document.addEventListener('dragover',event=>{
    const drop=event.target.closest('#panel-files .pm-drive-drop');
    if(drop){event.preventDefault();drop.classList.add('pm-drive-dragover');}
  });
  document.addEventListener('dragleave',event=>{
    const drop=event.target.closest('#panel-files .pm-drive-drop');
    if(drop)drop.classList.remove('pm-drive-dragover');
  });
  document.addEventListener('drop',event=>{
    const drop=event.target.closest('#panel-files .pm-drive-drop');
    if(!drop)return;
    event.preventDefault();
    drop.classList.remove('pm-drive-dragover');
    const input=qs(drop,'input[type=file]');
    if(input&&event.dataTransfer?.files?.length){
      try{input.files=event.dataTransfer.files;}catch(_ignore){}
    }
  });
  for(const root of roots())visibleRows(root);
  const observer=new MutationObserver(records=>{
    for(const record of records)for(const node of record.addedNodes){
      if(node.nodeType===1&&(node.matches?.('#panel-files .pm-drive-root')||
          node.querySelector?.('#panel-files .pm-drive-root'))){
        for(const root of roots())visibleRows(root);
        return;
      }
    }
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});
})();
</script>
'''

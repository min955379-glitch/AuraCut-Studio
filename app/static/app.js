function studio() {
  return {
    error:null, notice:'', files:[], checks:[], logs:[], dragging:false, uploading:false, progress:0,
    backing:false, generating:false, checking:false, gpuBusy:false, geminiBusy:false, geminiResult:null, topic:'', count:3, scenes:[], answered:'',
    async init(){ await Promise.all([this.refreshHealth(),this.refreshFiles()]); },
    status(name){ return this.checks.find(c=>c.name===name)?.status || 'WAIT'; },
    report(e, what){
      const data=e?.what_failed ? e : {what_failed:what,real_cause:e?.message || String(e),fix:'Check the server window and connection, then try again.'};
      this.error=data;
      fetch('/api/client-error',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).catch(()=>{});
    },
    async request(url, options={}) {
      let response;
      try { response=await fetch(url,options); } catch(e) { throw {what_failed:'Server connection',real_cause:e.message || String(e),fix:'Keep the AuraCut server window open and reload this page.'}; }
      let data;
      try { data=await response.json(); } catch(e) { throw {what_failed:'Server response',real_cause:'HTTP '+response.status+' returned invalid JSON: '+e.message,fix:'Check the AuraCut server window, then reload the page.'}; }
      if(!response.ok || data.ok===false) throw data.what_failed ? data : {what_failed:'Server response',real_cause:'HTTP '+response.status+' returned no error details.',fix:'Check the server window and retry.'};
      return data;
    },
    async refreshHealth(){this.checking=true;try{const r=await this.request('/api/health');this.checks=r.checks;if(this.geminiResult ? this.geminiResult.context===r.gemini_test_context : false){this.replaceCheck(this.geminiResult.check);}else{this.geminiResult=null;}}catch(e){this.report(e,'Health check');}finally{this.checking=false;}},
    replaceCheck(check){this.checks=this.checks.filter(c=>c.name!==check.name).concat([check]);},
    updateTimeoutWarning(warning){this.checks=this.checks.filter(c=>c.name!=='Gemini timeout');if(warning)this.checks.push(warning);},
    async geminiTest(){this.geminiBusy=true;this.error=null;this.geminiResult=null;try{const r=await this.request('/api/gemini-test',{method:'POST'});this.geminiResult={check:r.check,context:r.gemini_test_context};this.replaceCheck(r.check);this.updateTimeoutWarning(r.timeout_warning);if(r.check.status==='FAIL')this.report({what_failed:'Gemini test',real_cause:r.check.detail,fix:r.check.fix},'Gemini test');}catch(e){this.replaceCheck({name:'Gemini',status:'FAIL',detail:e.real_cause || String(e),fix:e.fix || 'Check the server window.'});this.report(e,'Gemini test');}finally{this.geminiBusy=false;}},
    async refreshFiles(){try{this.files=(await this.request('/api/files')).files;}catch(e){this.report(e,'File list');}},
    async refreshLogs(){try{this.logs=(await this.request('/api/logs')).lines;}catch(e){this.report(e,'Read logs');}},
    formatSize(n){return n<1048576 ? (n/1024).toFixed(1)+' KB' : (n/1048576).toFixed(1)+' MB';},
    uploadOne(file){return new Promise((resolve,reject)=>{
      const form=new FormData();form.append('file',file,file.name);const xhr=new XMLHttpRequest();
      xhr.open('POST','/api/upload');xhr.upload.onprogress=e=>{if(e.lengthComputable)this.progress=Math.round(e.loaded/e.total*100);};
      xhr.onerror=()=>reject({what_failed:'Upload',real_cause:'Connection to the local server was interrupted.',fix:'Keep the AuraCut server running and upload again.'});
      xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status<200||xhr.status>=300||data.ok===false)reject(data);else resolve(data);}catch(e){reject({what_failed:'Upload',real_cause:'Server returned invalid JSON: '+e.message,fix:'Check the server window and upload again.'});}};
      xhr.send(form);
    });},
    async uploadFiles(list){if(this.uploading)return;this.uploading=true;this.notice='';try{for(const file of Array.from(list)){this.progress=0;const r=await this.uploadOne(file);this.notice='Saved locally: '+r.name;}await this.refreshFiles();}catch(e){this.report(e,'Upload');await this.refreshFiles();}finally{this.uploading=false;}},
    async backup(file){this.backing=true;this.notice='';try{const r=await this.request('/api/backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder:file.folder,name:file.name})});this.notice=r.message;await this.refreshFiles();}catch(e){this.report(e,'Drive backup');}finally{this.backing=false;}},
    async generate(){this.error=null;this.scenes=[];this.answered='';this.generating=true;try{const r=await this.request('/api/script',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({topic:this.topic,scene_count:this.count})});this.scenes=r.scenes;this.answered=r.answered_by;this.updateTimeoutWarning(r.timeout_warning);}catch(e){this.report(e,'Script generation');}finally{this.generating=false;}},
    async gpuTest(){this.gpuBusy=true;try{const r=await this.request('/api/gpu-test',{method:'POST'});this.checks=this.checks.filter(c=>c.name!=='AMF encode test').concat([r.check]);}catch(e){this.checks=this.checks.filter(c=>c.name!=='AMF encode test').concat([{name:'AMF encode test',status:'FAIL',detail:e.real_cause || String(e),fix:e.fix || 'Check the server window.'}]);this.report(e,'GPU encode test');}finally{this.gpuBusy=false;}},
    async copy(text){try{await navigator.clipboard.writeText(text);this.notice='Prompt copied.';}catch(e){this.report({what_failed:'Copy prompt',real_cause:e.message || String(e),fix:'Select the prompt text and copy it manually.'},'Copy prompt');}}
  };
}

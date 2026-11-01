// Offline endpoint checks; no Google API calls or real credentials.
const fs=require('fs'), vm=require('vm'), assert=require('assert'), crypto=require('crypto');
const props=new Map(),files=new Map();let nextId=0;
function makeFile(name,text){const id=String(++nextId);const f={name,text,getId:()=>id,getBlob:()=>({getDataAsString:()=>f.text}),setContent:s=>{f.text=s;}};files.set(id,f);return f;}
const folder={getFoldersByName:()=>({hasNext:()=>false}),createFolder:()=>({getId:()=> 'test-archive-folder'}),getName:()=> 'test',getFilesByName:name=>{const list=[...files.values()].filter(f=>f.name===name);let i=0;return{hasNext:()=>i<list.length,next:()=>list[i++]};},createFile:makeFile};
const lock={waitLock(){},tryLock:()=>true,releaseLock(){},hasLock:()=>true};
const c={console:{log(){}},LockService:{getScriptLock:()=>lock},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v)})},DriveApp:{getFolderById:()=>folder,getFileById:id=>{if(!files.has(id))throw Error('missing');return files.get(id);}},Utilities:{getUuid:()=> 'fake-test-key',Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(_,value,charset)=>{assert.equal(charset,'utf8');return [...crypto.createHash('sha256').update(value,'utf8').digest()].map(b=>b>127?b-256:b);}},MimeType:{PLAIN_TEXT:'text/plain'},ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})}};
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/Memory.gs','utf8'),c);
c.setupMemory();c.setupMemory();assert.equal(files.size,4);
const secret=props.get('MISSY_MEMORY_SECRET'), main=files.get(props.get('MISSY_MEMORY_FILE_ID')),backup=files.get(props.get('MISSY_BACKUP_FILE_ID'));
const call=r=>c.doPost({postData:{contents:JSON.stringify(r)}});
const read=()=>call({action:'get',secret});
const put=(data,revision)=>call({action:'put',secret,data,expected_revision:revision});
assert.equal(call({action:'get',secret:'bad'}).error,'UNAUTHORIZED');
assert.equal(read().backup_ready,true);
const first={'факти':['original'],'фото':[],image_cache:{}};
assert.equal(put(first,0).revision,1);
assert.equal(put(first,0).error,'REVISION_CONFLICT');
const second={'факти':['original','new'],'фото':[],image_cache:{},conversations:{'1':{mode:'friend',version:1,messages:[]}}};
assert.equal(put(second,1).revision,2);
assert.equal(JSON.parse(backup.text).data['факти'].length,1);
main.text='broken JSON';const recovered=read();assert.equal(recovered.recovered,true);assert.equal(recovered.data['факти'][0],'original');assert.equal(JSON.parse(main.text).revision,1);
assert.equal(read().recovered,false);
main.text='broken';backup.text='also broken';assert.equal(read().error,'STORAGE_ERROR');assert.equal(main.text,'broken');
assert.equal(c.doGet().error,'POST_REQUIRED');
console.log('Backup endpoint checks passed: setup idempotence, authentication, revision conflicts, snapshot rotation, recovery and corrupt-backup refusal.');

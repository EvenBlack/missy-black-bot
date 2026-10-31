const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
let n=0;const files=new Map(),folders=new Map(),props=new Map();
function iterator(list){let i=0;return{hasNext:()=>i<list.length,next:()=>list[i++]};}
function makeFolder(){const id='folder-'+(++n),children=[];const folder={getId:()=>id,getName:()=> 'test',getFoldersByName:name=>iterator(children.filter(f=>f.folderName===name)),createFolder:name=>{const f=makeFolder();f.folderName=name;children.push(f);return f;},getFilesByName:name=>iterator(children.filter(f=>f.getName&&f.getName()===name)),getFiles:()=>iterator(children.filter(f=>f.getName)),createFile:(name,text)=>{let bytes;if(typeof name==='object'){bytes=Buffer.from(name.bytes);name=name.name;}else bytes=Buffer.from(text);const fid='file-'+(++n);const f={getId:()=>fid,getName:()=>name,getSize:()=>bytes.length,getBlob:()=>({getBytes:()=>[...bytes],getDataAsString:()=>bytes.toString()}),setContent:s=>{bytes=Buffer.from(s);}};children.push(f);files.set(fid,f);return f;}};folders.set(id,folder);return folder;}
const root=makeFolder(),lock={waitLock(){},tryLock:()=>true,releaseLock(){},hasLock:()=>true};
const c={console:{log(){}},LockService:{getScriptLock:()=>lock},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v)})},DriveApp:{getFolderById:id=>folders.get(id)||root,getFileById:id=>files.get(id)},Utilities:{getUuid:()=> 'fake-test-key',DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(_,bytes)=>[...crypto.createHash('sha256').update(Buffer.from(bytes)).digest()],base64Decode:s=>[...Buffer.from(s,'base64')],base64Encode:bytes=>Buffer.from(bytes).toString('base64'),newBlob:(bytes,mime,name)=>({bytes,mime,name})},MimeType:{PLAIN_TEXT:'text/plain'},ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})}};
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/Memory.gs','utf8'),c);
c.setupMemory();c.setupMemory();assert.equal(folders.size,2);assert.equal(files.size,2);
const secret=props.get('MISSY_MEMORY_SECRET');const call=r=>c.doPost({postData:{contents:JSON.stringify({...r,secret})}});
const bytes=Buffer.from('sample unchanged image bytes'),sha256=crypto.createHash('sha256').update(bytes).digest('hex'),archive_id=sha256.slice(0,24);
const upload={action:'archive_photo',owner:'7',sha256,content_b64:bytes.toString('base64'),mime:'image/png',ext:'png',filename:'original.png',caption:'Birthday beach',original:true,created:'2026-11-01T00:00:00+02:00'};
assert.equal(call(upload).sha256,sha256);assert.equal(files.size,4);
assert.equal(call(upload).archive_id,archive_id);assert.equal(files.size,4);
assert.equal(call({action:'get_photo',owner:'8',archive_id}).error,'PHOTO_NOT_FOUND');
let result=call({action:'get_photo',owner:'7',archive_id});assert.equal(result.content_b64,bytes.toString('base64'));assert.equal(result.filename,'original.png');
assert.equal(call({action:'list_photos',owner:'7',query:'birthday beach'}).photos.length,1);
assert.equal(call({action:'list_photos',owner:'7',query:'absent'}).photos.length,0);
assert.equal(call({action:'list_photos',owner:'8',query:''}).photos.length,0);
assert.equal(call({...upload,owner:'8'}).size,bytes.length);assert.equal(call({action:'list_photos',owner:'7'}).photos.length,1);
assert.equal(call({...upload,sha256:'a'.repeat(64)}).error,'INVALID_PHOTO');
const folder=folders.get(props.get('MISSY_PHOTO_FOLDER_ID'));const binary=folder.getFilesByName('7_'+archive_id+'.png').next();binary.setContent('tampered');
assert.equal(call({action:'get_photo',owner:'7',archive_id}).error,'PHOTO_CONFLICT');
// A metadata failure after binary creation must be retryable without duplicate bytes.
const originalCreate=folder.createFile;let fail=true;
folder.createFile=(name,...args)=>{if(fail&&typeof name==='string'&&name.startsWith('9_')&&name.endsWith('.json')){fail=false;throw Error('simulated failure');}return originalCreate(name,...args);};
assert.equal(call({...upload,owner:'9'}).error,'STORAGE_ERROR');const count=files.size;
assert.equal(call({...upload,owner:'9'}).sha256,sha256);assert.equal(files.size,count+1);
console.log('Photo endpoint checks passed: original bytes, idempotent upload, user isolation, caption search, checksum rejection and retry after partial failure.');

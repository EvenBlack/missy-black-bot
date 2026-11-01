const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto');
let n=0;const files=new Map(),folders=new Map(),props=new Map();
function iterator(list){let i=0;return{hasNext:()=>i<list.length,next:()=>list[i++]};}
function makeFolder(){const id='folder-'+(++n),children=[];const folder={getId:()=>id,getName:()=> 'test',getFoldersByName:name=>iterator(children.filter(f=>f.folderName===name)),createFolder:name=>{const f=makeFolder();f.folderName=name;children.push(f);return f;},getFilesByName:name=>iterator(children.filter(f=>f.getName&&f.getName()===name)),getFiles:()=>iterator(children.filter(f=>f.getName)),createFile:(name,text)=>{let bytes;if(typeof name==='object'){bytes=Buffer.from(name.bytes);name=name.name;}else bytes=Buffer.from(text);const fid='file-'+(++n);const f={getId:()=>fid,getName:()=>name,getSize:()=>bytes.length,getBlob:()=>({getBytes:()=>[...bytes],getContentType:()=> 'image/png',getDataAsString:()=>bytes.toString()}),setContent:s=>{bytes=Buffer.from(s);}};children.push(f);files.set(fid,f);return f;}};folders.set(id,folder);return folder;}
const root=makeFolder(),lock={waitLock(){},tryLock:()=>true,releaseLock(){},hasLock:()=>true};
const c={console:{log(){}},LockService:{getScriptLock:()=>lock},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k),setProperty:(k,v)=>props.set(k,v)})},DriveApp:{getFolderById:id=>folders.get(id)||root,getFileById:id=>files.get(id)},Utilities:{getUuid:()=> 'fake-test-key',Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(_,bytes,charset)=>{if(typeof bytes==='string')assert.equal(charset,'utf8','Text digests must specify UTF-8 explicitly');return [...crypto.createHash('sha256').update(Buffer.from(bytes)).digest()].map(b=>b>127?b-256:b);},base64Decode:s=>[...Buffer.from(s,'base64')],base64Encode:bytes=>Buffer.from(bytes).toString('base64'),newBlob:(bytes,mime,name)=>({bytes,mime,name})},MimeType:{PLAIN_TEXT:'text/plain'},ContentService:{MimeType:{JSON:'json'},createTextOutput:s=>({setMimeType:()=>JSON.parse(s)})}};
vm.createContext(c);vm.runInContext(fs.readFileSync(__dirname+'/Memory.gs','utf8'),c);
c.setupMemory();c.setupMemory();assert.equal(folders.size,8);assert.equal(files.size,2);
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

const identityFolder=folders.get(props.get('MISSY_IDENTITY_FOLDER_ID'));
assert.equal(identityFolder.folderName,'Katrusia_Images');
assert.equal(call({action:'get_identity'}).error,'IDENTITY_NOT_SET');
assert.equal(call({action:'set_identity',owner:'8',archive_id}).archive_id,archive_id);
const afterSelection=files.size;
assert.equal(call({action:'set_identity',owner:'8',archive_id}).archive_id,archive_id);
assert.equal(files.size,afterSelection);
assert.equal(call({action:'get_identity'}).content_b64,bytes.toString('base64'));
folder.getFilesByName('8_'+archive_id+'.png').next().setContent('source changed');
assert.equal(call({action:'get_identity'}).content_b64,bytes.toString('base64'));
assert.equal(call({action:'set_identity',owner:'100',archive_id}).error,'PHOTO_NOT_FOUND');
console.log('Identity checks passed: Latin folder, explicit selection, idempotent copy and retrieval independent of original archive bytes.');

// Voice storage: immutable transcript/audio records, search, photo links and retries.
assert.equal(call({action:'get'}).voice_ready,true);
const audio=Buffer.from('OggS original voice'),audioSha=crypto.createHash('sha256').update(audio).digest('hex');
function voicePayload(category,transcript,photo_id='') {
  const note_id=crypto.createHash('sha256').update(audioSha+'\n'+category+'\n'+photo_id+'\n'+transcript).digest('hex').slice(0,24);
  return {action:'store_voice',owner:'10',sha256:audioSha,content_b64:audio.toString('base64'),category,transcript,photo_id,note_id,created:'2026-11-01'};
}
const voice=voicePayload('memories','Мій спогад про море');
assert.equal(c.doPost({postData:{contents:JSON.stringify({...voice,secret:'bad'})}}).error,'UNAUTHORIZED');
assert.equal(call(voice).note_id,voice.note_id);let voiceCount=files.size;
assert.equal(call(voice).size,audio.length);assert.equal(files.size,voiceCount);
assert.equal(call({action:'get_voice',owner:'10',category:'memories',note_id:voice.note_id}).transcript,voice.transcript);
assert.equal(call({action:'get_voice',owner:'11',category:'memories',note_id:voice.note_id}).error,'VOICE_NOT_FOUND');
assert.equal(call({action:'list_voice',owner:'10',query:'море'}).notes.length,1);
assert.equal(call({action:'list_voice',owner:'11',query:'море'}).notes.length,0);
assert.equal(call({...voice,transcript:'changed'}).error,'VOICE_KEY_MISMATCH');
assert.equal(call({...voice,sha256:'a'.repeat(64)}).error,'VOICE_KEY_MISMATCH');
const vf=folders.get(props.get('MISSY_VOICE_MEMORIES_ID'));
assert.equal(vf.folderName,'Voice_Memories');
assert.equal(vf.getFilesByName('10_'+voice.note_id+'.ogg').next().getBlob().getDataAsString(),audio.toString());
const vt=vf.getFilesByName('10_'+voice.note_id+'.txt').next();vt.setContent('corrupted');
assert.equal(call({action:'get_voice',owner:'10',category:'memories',note_id:voice.note_id}).error,'VOICE_CONFLICT');
assert.equal(call(voice).error,'VOICE_CONFLICT');
assert.equal(call({...upload,owner:'10'}).archive_id,archive_id);
const description=voicePayload('photos','Родина на березі моря',archive_id);
assert.equal(call(description).note_id,description.note_id);
assert.equal(call({action:'list_photos',owner:'10',query:'родина'}).photos.length,1);
assert(call({action:'get_photo',owner:'10',archive_id}).caption.includes('Родина'));
assert.equal(call({...upload,owner:'10'}).archive_id,archive_id);
assert.equal(call({action:'list_photos',owner:'10',query:'родина'}).photos.length,1);
assert.equal(call({...description,owner:'11'}).error,'PHOTO_NOT_FOUND');
const nf=folders.get(props.get('MISSY_VOICE_NOTES_ID')),oldCreate=nf.createFile;let once=true;
nf.createFile=(name,...args)=>{if(once&&typeof name==='string'&&name.endsWith('.json')){once=false;throw Error('partial');}return oldCreate(name,...args);};
const retry=voicePayload('notes','Точна нотатка');
assert.equal(call(retry).error,'STORAGE_ERROR');voiceCount=files.size;
assert.equal(call(retry).note_id,retry.note_id);assert.equal(files.size,voiceCount+1);
console.log('Voice checks passed: Latin folders, original audio/transcript, user isolation, idempotence, search, tamper rejection, photo description and partial-write retry.');

const unicode=voicePayload('notes','Український текст 🇺🇦\nНовий рядок');assert.equal(call(unicode).note_id,unicode.note_id);
assert.equal(call({...unicode,content_b64:Buffer.from('changed bytes').toString('base64')}).error,'VOICE_AUDIO_CHECKSUM');

// Full history uses private owner folders, immutable events and paged day shards.
function history(owner,id,text,created='2026-11-01T01:00:00Z') {
 const record_json=JSON.stringify({id,role:id.startsWith('assistant')?'assistant':'user',text,created,kind:'text',telegram:{text}});
 return {action:'append_history',owner,record_json,digest:crypto.createHash('sha256').update(record_json).digest('hex')};
}
const h=history('71','user:123','Мій спогад про синю чашку 🇺🇦','2026-10-24T01:00:00Z');
assert.equal(call(h).id,'user:123');const hc=files.size;
assert.equal(call(h).digest,h.digest);assert.equal(files.size,hc);
assert.equal(call(history('71','assistant:124','Я запам’ятала синю чашку.','2026-10-24T01:00:01Z')).id,'assistant:124');
assert.equal(call(history('72','user:123','PRIVATE_SECOND_PERSON')).id,'user:123');
let hr=call({action:'search_history',owner:'71',query:'чашку'});
assert.equal(hr.messages.length,2);assert(!JSON.stringify(hr).includes('PRIVATE_SECOND_PERSON'));
assert.equal(call({action:'search_history',owner:'72',query:'чашку'}).messages.length,0);
assert.equal(call(history('71','user:123','Changed text','2026-10-24T01:00:00Z')).error,'HISTORY_CONFLICT');
assert.equal(call({...h,digest:'bad'}).error,'INVALID_HISTORY');
for(let i=0;i<35;i++) call(history('73','user:'+i,'record '+i,new Date(Date.UTC(2026,8,1+i)).toISOString()));
hr=call({action:'search_history',owner:'73',query:''});assert.equal(hr.next_cursor,30);
assert.equal(call({action:'search_history',owner:'73',query:'',cursor:30}).next_cursor,null);
console.log('History checks passed: immutable full text, signed UTF-8 checksums, owner separation, exact retries, past-week recall and paging.');

// Book upload and indexing are separate; only a complete verified index is visible.
const bookBytes=Buffer.from('test book original'),bookSha=crypto.createHash('sha256').update(bookBytes).digest('hex'),bookId=bookSha.slice(0,24);
const bp={action:'store_book',owner:'71',book_id:bookId,sha256:bookSha,ext:'epub',title:'Книга про море',author:'Автор',filename:'book.epub',content_b64:bookBytes.toString('base64'),created:'2026-11-01'};
assert.equal(call(bp).sha256,bookSha);const bc=files.size;assert.equal(call(bp).size,bookBytes.length);assert.equal(files.size,bc);
assert.equal(call({action:'list_books',owner:'71'}).books.length,0);
assert.equal(call({action:'get_book',owner:'71',book_id:bookId}).error,'BOOK_INDEX_PENDING');
const index_json=JSON.stringify({version:1,chunks:[{chapter:'Початок',text:'Синя чашка і море.'},{chapter:'Кінець',text:'Дівчина знайшла подарунок.'}]});
const bi={action:'index_book',owner:'71',book_id:bookId,index_json,index_digest:crypto.createHash('sha256').update(index_json).digest('hex')};
assert.equal(call({...bi,index_digest:'bad'}).error,'INVALID_BOOK_INDEX');
assert.equal(call(bi).chunks,2);assert.equal(call(bi).chunks,2);
assert.equal(call({action:'list_books',owner:'71',query:'море'}).books.length,1);
assert.equal(call({action:'list_books',owner:'72'}).books.length,0);
assert.equal(call({action:'book_context',owner:'72',book_id:bookId,query:'чашка'}).error,'BOOK_NOT_FOUND');
let br=call({action:'book_context',owner:'71',book_id:bookId,query:'чашка'});assert.equal(br.chunks.length,1);assert.equal(br.chunks[0].number,1);
br=call({action:'book_context',owner:'71',book_id:bookId,query:'підсумуй'});assert.equal(br.selection,'sampled_overview');assert.equal(br.chunks.length,2);
br=call({action:'book_context',owner:'71',book_id:bookId,query:'',chunk_number:2});assert.equal(br.chunks[0].text,'Дівчина знайшла подарунок.');
assert.equal(call({action:'get_book',owner:'71',book_id:bookId}).content_b64,bookBytes.toString('base64'));
const bf=folders.get(props.get('MISSY_BOOKS_FOLDER_ID'));const own=bf.getFoldersByName('71').next();
own.getFilesByName(bookId+'.index.json').next().setContent('broken');
assert.equal(call({action:'book_context',owner:'71',book_id:bookId,query:''}).error,'BOOK_CHECKSUM');
assert.equal(call(bi).chunks,2);
console.log('Book checks passed: staged import, verified index before listing, UTF-8, owner isolation, original bytes, keyword retrieval, overview samples and repair after index corruption.');

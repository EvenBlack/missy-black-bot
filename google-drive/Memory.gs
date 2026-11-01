/** Missy memory storage. Run setupMemory once, then deploy as a web app. */
const MISSY_FOLDER_ID = '1rNoUkxNvYh_r8_ouYxOlnIpJ4OG1mHvC';

function setupMemory() {
  testVoiceProtocol();
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const props = PropertiesService.getScriptProperties();
    const folder = DriveApp.getFolderById(MISSY_FOLDER_ID);
    folder.getName(); // Verify the signed-in owner can access the intended folder.
    if (!props.getProperty('MISSY_MEMORY_SECRET')) {
      props.setProperty('MISSY_MEMORY_SECRET', Utilities.getUuid() + Utilities.getUuid());
    }
    if (!props.getProperty('MISSY_MEMORY_FILE_ID')) {
      const files = folder.getFilesByName('missy_memory_store.json');
      let file;
      if (files.hasNext()) {
        file = files.next();
        if (files.hasNext()) throw new Error('Duplicate memory files: resolve manually.');
        readStore_(file); // Never replace an existing invalid file with an empty one.
      } else {
        file = folder.createFile('missy_memory_store.json', JSON.stringify({revision: 0, data: null}), MimeType.PLAIN_TEXT);
      }
      props.setProperty('MISSY_MEMORY_FILE_ID', file.getId());
    }
    if (!props.getProperty('MISSY_BACKUP_FILE_ID')) {
      const main = DriveApp.getFileById(props.getProperty('MISSY_MEMORY_FILE_ID'));
      const snapshot = JSON.stringify(readStore_(main));
      const found = folder.getFilesByName('missy_memory_backup.json');
      let backup;
      if (found.hasNext()) {
        backup = found.next();
        if (found.hasNext()) throw new Error('Duplicate backup files.');
        readStore_(backup);
      } else {
        backup = folder.createFile('missy_memory_backup.json', snapshot, MimeType.PLAIN_TEXT);
      }
      props.setProperty('MISSY_BACKUP_FILE_ID', backup.getId());
    }
    if (!props.getProperty('MISSY_PHOTO_FOLDER_ID')) {
      const folders = folder.getFoldersByName('Missy_Photo_Archive');
      let archive;
      if (folders.hasNext()) {
        archive = folders.next();
        if (folders.hasNext()) throw new Error('Duplicate photo archive folders.');
      } else {
        archive = folder.createFolder('Missy_Photo_Archive');
      }
      props.setProperty('MISSY_PHOTO_FOLDER_ID', archive.getId());
    }
    if (!props.getProperty('MISSY_IDENTITY_FOLDER_ID')) {
      const folders = folder.getFoldersByName('Katrusia_Images');
      let images;
      if (folders.hasNext()) {
        images = folders.next();
        if (folders.hasNext()) throw new Error('Duplicate identity folders.');
      } else {
        images = folder.createFolder('Katrusia_Images');
      }
      props.setProperty('MISSY_IDENTITY_FOLDER_ID', images.getId());
    }
    Object.keys(VOICE_FOLDERS).forEach(category => {
      const key = 'MISSY_VOICE_' + category.toUpperCase() + '_ID';
      if (!props.getProperty(key)) {
        const found = folder.getFoldersByName(VOICE_FOLDERS[category]);
        const selected = found.hasNext() ? found.next() : folder.createFolder(VOICE_FOLDERS[category]);
        if (found.hasNext()) throw new Error('Duplicate voice folders.');
        props.setProperty(key, selected.getId());
      }
    });
    console.log('Setup complete. Secret is in Project Settings > Script properties. Do not paste it into chat.');
  } finally {
    lock.releaseLock();
  }
}

function json_(value) {
  return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON);
}

function readStore_(file) {
  const store = JSON.parse(file.getBlob().getDataAsString('UTF-8'));
  if (!Number.isSafeInteger(store.revision) || store.revision < 0 || !Object.prototype.hasOwnProperty.call(store, 'data')) {
    throw new Error('Invalid stored memory.');
  }
  if (store.data !== null && (typeof store.data !== 'object' || Array.isArray(store.data) || !Array.isArray(store.data['факти']) || !Array.isArray(store.data['фото']) || !store.data.image_cache || typeof store.data.image_cache !== 'object' || Array.isArray(store.data.image_cache))) {
    throw new Error('Invalid stored data.');
  }
  return store;
}

function sameSecret_(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string' || !a || a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

function doGet() {
  // No memory, tokens or file IDs are returned to unauthenticated GET requests.
  return json_({ok: false, error: 'POST_REQUIRED'});
}

function doPost(e) {
  let lock;
  try {
    const raw = e && e.postData && e.postData.contents;
    if (!raw || raw.length > 12 * 1024 * 1024) return json_({ok: false, error: 'INVALID_REQUEST'});
    const request = JSON.parse(raw);
    const props = PropertiesService.getScriptProperties();
    if (!sameSecret_(request.secret, props.getProperty('MISSY_MEMORY_SECRET'))) {
      return json_({ok: false, error: 'UNAUTHORIZED'});
    }
    if (!['get', 'put', 'archive_photo', 'get_photo', 'list_photos', 'set_identity', 'get_identity', 'store_voice', 'list_voice', 'get_voice'].includes(request.action)) return json_({ok: false, error: 'INVALID_ACTION'});
    const fileId = props.getProperty('MISSY_MEMORY_FILE_ID');
    if (!fileId) return json_({ok: false, error: 'SETUP_REQUIRED'});
    lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) return json_({ok: false, error: 'BUSY'});
    if (['store_voice', 'list_voice', 'get_voice'].includes(request.action)) return voiceRequest_(request, props);
    if (['set_identity', 'get_identity'].includes(request.action)) {
      return identityRequest_(request, props);
    }
    if (['archive_photo', 'get_photo', 'list_photos'].includes(request.action)) {
      return photoRequest_(request, props);
    }
    const backupId = props.getProperty('MISSY_BACKUP_FILE_ID');
    const file = DriveApp.getFileById(fileId);
    let store;
    let recovered = false;
    try {
      store = readStore_(file);
    } catch (mainError) {
      if (!backupId) return json_({ok: false, error: 'BACKUP_UNAVAILABLE'});
      store = readStore_(DriveApp.getFileById(backupId));
      // Never restore an uninitialized backup over an established memory file.
      if (store.data === null) return json_({ok: false, error: 'BACKUP_UNAVAILABLE'});
      file.setContent(JSON.stringify(store));
      recovered = true;
    }
    if (request.action === 'get') return json_({ok: true, revision: store.revision, data: store.data, backup_ready: Boolean(backupId), photo_archive_ready: Boolean(props.getProperty('MISSY_PHOTO_FOLDER_ID')), identity_ready: Boolean(props.getProperty('MISSY_IDENTITY_FOLDER_ID')), voice_protocol: 2, voice_ready: Object.keys(VOICE_FOLDERS).every(k => Boolean(props.getProperty('MISSY_VOICE_' + k.toUpperCase() + '_ID'))), recovered: recovered});
    if (!Number.isSafeInteger(request.expected_revision) || request.expected_revision !== store.revision) {
      return json_({ok: false, error: 'REVISION_CONFLICT'});
    }
    const data = request.data;
    if (!data || typeof data !== 'object' || Array.isArray(data) || !Array.isArray(data['факти']) || !Array.isArray(data['фото']) || typeof data.image_cache !== 'object' || !data.image_cache || Array.isArray(data.image_cache)) {
      return json_({ok: false, error: 'INVALID_DATA'});
    }
    const next = {revision: store.revision + 1, data: data};
    const serialized = JSON.stringify(next);
    if (serialized.length > 262144) return json_({ok: false, error: 'MEMORY_TOO_LARGE'});
    if (!backupId) return json_({ok: false, error: 'BACKUP_SETUP_REQUIRED'});
    // Preserve the last good complete snapshot before replacing the main file.
    // A failed backup write aborts the update; it must never be acknowledged.
    DriveApp.getFileById(backupId).setContent(JSON.stringify(store));
    file.setContent(serialized);
    return json_({ok: true, revision: next.revision});
  } catch (error) {
    // Internal exceptions may contain Drive paths or IDs; do not return/log payloads.
    return json_({ok: false, error: 'STORAGE_ERROR'});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}


function exactPhotoFile_(folder, name) {
  const found = folder.getFilesByName(name);
  if (!found.hasNext()) return null;
  const file = found.next();
  if (found.hasNext()) throw new Error('Duplicate archive entries.');
  return file;
}

function photoDigest_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes)
    .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('');
}

function photoRequest_(request, props) {
  const folderId = props.getProperty('MISSY_PHOTO_FOLDER_ID');
  if (!folderId) return json_({ok: false, error: 'ARCHIVE_SETUP_REQUIRED'});
  const owner = request.owner;
  if (typeof owner !== 'string' || !/^[0-9]{1,20}$/.test(owner)) return json_({ok: false, error: 'INVALID_PHOTO'});
  const folder = DriveApp.getFolderById(folderId);
  if (request.action === 'list_photos') {
    const query = String(request.query || '').slice(0,150).toLowerCase().split(/\s+/).filter(Boolean);
    const found = folder.getFiles();
    const photos = [];
    let unreadable = 0;
    while (found.hasNext()) {
      const file = found.next();
      if (!file.getName().startsWith(owner + '_') || !file.getName().endsWith('.json')) continue;
      try {
        const meta = JSON.parse(file.getBlob().getDataAsString('UTF-8'));
        if (meta.owner !== owner || !/^[a-f0-9]{24}$/.test(meta.archive_id)) continue;
        const text = String(meta.caption || '') + ' ' + String(meta.description || '') + ' ' + String(meta.filename || '') + ' ' + String(meta.created || '');
        if (query.every(word => text.toLowerCase().includes(word))) {
          photos.push({archive_id:meta.archive_id, caption:[meta.caption, meta.description].filter(Boolean).join(' — '), filename:meta.filename, created:meta.created, original:meta.original});
        }
      } catch (error) { unreadable += 1; }
    }
    photos.sort((a,b) => String(b.created).localeCompare(String(a.created)));
    return json_({ok:true, photos:photos.slice(0,10), has_more:photos.length>10, unreadable_count:unreadable});
  }
  if (request.action === 'archive_photo') {
    const mimeByExt = {jpg:'image/jpeg',png:'image/png',webp:'image/webp',gif:'image/gif',tiff:'image/tiff',bmp:'image/bmp'};
    if (typeof request.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(request.sha256) || mimeByExt[request.ext] !== request.mime || typeof request.content_b64 !== 'string' || request.content_b64.length > 11200000 || !/^[A-Za-z0-9+/]*={0,2}$/.test(request.content_b64) || request.content_b64.length % 4) {
      return json_({ok:false,error:'INVALID_PHOTO'});
    }
    const bytes = Utilities.base64Decode(request.content_b64);
    if (!bytes.length || bytes.length > 8*1024*1024 || photoDigest_(bytes) !== request.sha256) return json_({ok:false,error:'INVALID_PHOTO'});
    const archiveId = request.sha256.slice(0,24);
    const stem = owner + '_' + archiveId;
    const metaFile = exactPhotoFile_(folder, stem + '.json');
    if (metaFile) {
      const old = JSON.parse(metaFile.getBlob().getDataAsString('UTF-8'));
      if (old.sha256 !== request.sha256 || old.owner !== owner) return json_({ok:false,error:'PHOTO_CONFLICT'});
    }
    let binary = exactPhotoFile_(folder, stem + '.' + request.ext);
    if (!binary) binary = folder.createFile(Utilities.newBlob(bytes, request.mime, stem + '.' + request.ext));
    const saved = binary.getBlob().getBytes();
    if (saved.length !== bytes.length || photoDigest_(saved) !== request.sha256) return json_({ok:false,error:'PHOTO_CONFLICT'});
    const meta = {owner:owner,archive_id:archiveId,sha256:request.sha256,size:bytes.length,filename:String(request.filename || 'photo.'+request.ext).slice(0,100),caption:String(request.caption || '').slice(0,1000),original:request.original===true,created:String(request.created || new Date().toISOString()).slice(0,50),ext:request.ext};
    if (metaFile) { meta.description = JSON.parse(metaFile.getBlob().getDataAsString('UTF-8')).description || ''; metaFile.setContent(JSON.stringify(meta)); }
    else folder.createFile(stem + '.json', JSON.stringify(meta), MimeType.PLAIN_TEXT);
    return json_({ok:true,archive_id:archiveId,sha256:request.sha256,size:bytes.length});
  }
  if (typeof request.archive_id !== 'string' || !/^[a-f0-9]{24}$/.test(request.archive_id)) return json_({ok:false,error:'INVALID_PHOTO'});
  const stem = owner + '_' + request.archive_id;
  const sidecar = exactPhotoFile_(folder, stem + '.json');
  if (!sidecar) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
  const meta = JSON.parse(sidecar.getBlob().getDataAsString('UTF-8'));
  if (meta.owner !== owner || meta.archive_id !== request.archive_id || !['jpg','png','webp','gif','tiff','bmp'].includes(meta.ext)) return json_({ok:false,error:'INVALID_PHOTO'});
  const file = exactPhotoFile_(folder, stem + '.' + meta.ext);
  if (!file || file.getSize() > 8*1024*1024) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
  const bytes = file.getBlob().getBytes();
  if (photoDigest_(bytes) !== meta.sha256) return json_({ok:false,error:'PHOTO_CONFLICT'});
  return json_({ok:true,content_b64:Utilities.base64Encode(bytes),sha256:meta.sha256,filename:meta.filename,caption:[meta.caption, meta.description].filter(Boolean).join(' — ')});
}


function identityRequest_(request, props) {
  const folderId = props.getProperty('MISSY_IDENTITY_FOLDER_ID');
  if (!folderId) return json_({ok:false,error:'IDENTITY_SETUP_REQUIRED'});
  const images = DriveApp.getFolderById(folderId);
  if (request.action === 'set_identity') {
    if (typeof request.owner !== 'string' || !/^[0-9]{1,20}$/.test(request.owner) || typeof request.archive_id !== 'string' || !/^[a-f0-9]{24}$/.test(request.archive_id)) return json_({ok:false,error:'INVALID_PHOTO'});
    const archive = DriveApp.getFolderById(props.getProperty('MISSY_PHOTO_FOLDER_ID'));
    const stem = request.owner + '_' + request.archive_id;
    const sidecar = exactPhotoFile_(archive, stem + '.json');
    if (!sidecar) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
    const meta = JSON.parse(sidecar.getBlob().getDataAsString('UTF-8'));
    if (meta.owner !== request.owner || meta.archive_id !== request.archive_id || !['jpg','png','webp','gif','tiff','bmp'].includes(meta.ext)) return json_({ok:false,error:'INVALID_PHOTO'});
    const source = exactPhotoFile_(archive, stem + '.' + meta.ext);
    if (!source || source.getSize() > 8*1024*1024) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
    const bytes = source.getBlob().getBytes();
    if (photoDigest_(bytes) !== meta.sha256) return json_({ok:false,error:'PHOTO_CONFLICT'});
    const name = 'Katrusia_' + request.archive_id + '.' + meta.ext;
    let copy = exactPhotoFile_(images, name);
    if (!copy) copy = images.createFile(Utilities.newBlob(bytes, source.getBlob().getContentType(), name));
    if (photoDigest_(copy.getBlob().getBytes()) !== meta.sha256) return json_({ok:false,error:'PHOTO_CONFLICT'});
    const selected = {archive_id:meta.archive_id,sha256:meta.sha256,ext:meta.ext,filename:'Katrusia.'+meta.ext,caption:'Катруся — основний візуальний образ',size:bytes.length};
    const primary = exactPhotoFile_(images, 'primary.json');
    if (primary) primary.setContent(JSON.stringify(selected));
    else images.createFile('primary.json', JSON.stringify(selected), MimeType.PLAIN_TEXT);
    return json_({ok:true,archive_id:meta.archive_id,sha256:meta.sha256});
  }
  const primary = exactPhotoFile_(images, 'primary.json');
  if (!primary) return json_({ok:false,error:'IDENTITY_NOT_SET'});
  const selected = JSON.parse(primary.getBlob().getDataAsString('UTF-8'));
  if (!/^[a-f0-9]{24}$/.test(selected.archive_id) || !['jpg','png','webp','gif','tiff','bmp'].includes(selected.ext)) return json_({ok:false,error:'INVALID_PHOTO'});
  const copy = exactPhotoFile_(images, 'Katrusia_' + selected.archive_id + '.' + selected.ext);
  if (!copy || copy.getSize() > 8*1024*1024) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
  const bytes = copy.getBlob().getBytes();
  if (photoDigest_(bytes) !== selected.sha256) return json_({ok:false,error:'PHOTO_CONFLICT'});
  return json_({ok:true,archive_id:selected.archive_id,sha256:selected.sha256,content_b64:Utilities.base64Encode(bytes),filename:selected.filename,caption:selected.caption});
}

const VOICE_FOLDERS = {memories:'Voice_Memories', notes:'Voice_Notes', photos:'Photo_Descriptions'};

function voiceRequest_(request, props) {
  const owner = request.owner;
  if (typeof owner !== 'string' || !/^[0-9]{1,20}$/.test(owner)) return json_({ok:false,error:'INVALID_VOICE'});
  if (!Object.keys(VOICE_FOLDERS).every(k => props.getProperty('MISSY_VOICE_' + k.toUpperCase() + '_ID'))) return json_({ok:false,error:'VOICE_SETUP_REQUIRED'});
  if (request.action === 'list_voice') {
    const words = String(request.query || '').slice(0,150).toLowerCase().split(/\s+/).filter(Boolean);
    const notes = []; let unreadable = 0;
    Object.keys(VOICE_FOLDERS).forEach(category => {
      const files = DriveApp.getFolderById(props.getProperty('MISSY_VOICE_' + category.toUpperCase() + '_ID')).getFiles();
      while (files.hasNext()) {
        const file = files.next();
        if (!file.getName().startsWith(owner + '_') || !file.getName().endsWith('.json')) continue;
        try {
          const m = JSON.parse(file.getBlob().getDataAsString('UTF-8'));
          if (m.owner !== owner || m.category !== category || !/^[a-f0-9]{24}$/.test(m.note_id) || typeof m.transcript !== 'string') throw Error('Invalid note');
          if (words.every(w => (m.transcript + ' ' + m.created).toLowerCase().includes(w))) notes.push({note_id:m.note_id,category:category,created:m.created,excerpt:m.transcript.slice(0,200)});
        } catch (e) { unreadable += 1; }
      }
    });
    notes.sort((a,b) => String(b.created).localeCompare(String(a.created)));
    return json_({ok:true,notes:notes.slice(0,10),has_more:notes.length>10,unreadable_count:unreadable});
  }
  const category = request.category;
  if (!Object.prototype.hasOwnProperty.call(VOICE_FOLDERS, category) || typeof request.note_id !== 'string' || !/^[a-f0-9]{24}$/.test(request.note_id)) return json_({ok:false,error:'INVALID_VOICE'});
  const folder = DriveApp.getFolderById(props.getProperty('MISSY_VOICE_' + category.toUpperCase() + '_ID'));
  const stem = owner + '_' + request.note_id;
  const sidecar = exactPhotoFile_(folder, stem + '.json');
  if (request.action === 'get_voice') {
    if (!sidecar) return json_({ok:false,error:'VOICE_NOT_FOUND'});
    const m = JSON.parse(sidecar.getBlob().getDataAsString('UTF-8'));
    const text = exactPhotoFile_(folder, stem + '.txt');
    if (m.owner !== owner || m.category !== category || m.note_id !== request.note_id || typeof m.transcript !== 'string' || !text || text.getBlob().getDataAsString('UTF-8') !== m.transcript) return json_({ok:false,error:'VOICE_CONFLICT'});
    return json_({ok:true,note_id:m.note_id,category:m.category,transcript:m.transcript,created:m.created,photo_id:m.photo_id});
  }
  if (typeof request.transcript !== 'string' || !request.transcript.trim() || request.transcript.length > 80000 || typeof request.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(request.sha256) || typeof request.content_b64 !== 'string' || request.content_b64.length > 11200000 || request.content_b64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(request.content_b64)) return json_({ok:false,error:'INVALID_VOICE'});
  const photoId = request.photo_id || '';
  if ((photoId && !/^[a-f0-9]{24}$/.test(photoId)) || (category === 'photos' && !photoId) || (category !== 'photos' && photoId)) return json_({ok:false,error:'INVALID_VOICE'});
  const key = voiceKey_(request.sha256, category, photoId, request.transcript);
  if (key !== request.note_id) return json_({ok:false,error:'VOICE_KEY_MISMATCH'});
  const bytes = Utilities.base64Decode(request.content_b64);
  if (!bytes.length || bytes.length > 8*1024*1024) return json_({ok:false,error:'VOICE_AUDIO_SIZE'});
  if (photoDigest_(bytes) !== request.sha256) return json_({ok:false,error:'VOICE_AUDIO_CHECKSUM'});
  let photoSidecar, photoMeta;
  if (photoId) {
    photoSidecar = exactPhotoFile_(DriveApp.getFolderById(props.getProperty('MISSY_PHOTO_FOLDER_ID')), owner + '_' + photoId + '.json');
    if (!photoSidecar) return json_({ok:false,error:'PHOTO_NOT_FOUND'});
    photoMeta = JSON.parse(photoSidecar.getBlob().getDataAsString('UTF-8'));
    if (photoMeta.owner !== owner || photoMeta.archive_id !== photoId) return json_({ok:false,error:'INVALID_PHOTO'});
  }
  if (sidecar) {
    const old = JSON.parse(sidecar.getBlob().getDataAsString('UTF-8'));
    if (old.owner !== owner || old.sha256 !== request.sha256 || old.transcript !== request.transcript || old.category !== category || old.photo_id !== photoId) return json_({ok:false,error:'VOICE_CONFLICT'});
  }
  let binary = exactPhotoFile_(folder, stem + '.ogg');
  if (!binary) binary = folder.createFile(Utilities.newBlob(bytes, 'audio/ogg', stem + '.ogg'));
  if (photoDigest_(binary.getBlob().getBytes()) !== request.sha256) return json_({ok:false,error:'VOICE_CONFLICT'});
  let text = exactPhotoFile_(folder, stem + '.txt');
  if (!text) text = folder.createFile(stem + '.txt', request.transcript, MimeType.PLAIN_TEXT);
  if (text.getBlob().getDataAsString('UTF-8') !== request.transcript) return json_({ok:false,error:'VOICE_CONFLICT'});
  const meta = {owner:owner,note_id:key,sha256:request.sha256,size:bytes.length,transcript:request.transcript,category:category,photo_id:photoId,created:String(request.created || new Date().toISOString()).slice(0,50)};
  if (!sidecar) folder.createFile(stem + '.json', JSON.stringify(meta), MimeType.PLAIN_TEXT);
  const savedMeta = JSON.parse(exactPhotoFile_(folder, stem + '.json').getBlob().getDataAsString('UTF-8'));
  if (savedMeta.transcript !== request.transcript || savedMeta.sha256 !== request.sha256) return json_({ok:false,error:'VOICE_CONFLICT'});
  if (photoSidecar) {
    photoMeta.description = 'Голосовий опис: ' + request.transcript.slice(0,4000);
    photoMeta.description_note = category + ':' + key;
    photoSidecar.setContent(JSON.stringify(photoMeta));
    if (JSON.parse(photoSidecar.getBlob().getDataAsString('UTF-8')).description_note !== category + ':' + key) return json_({ok:false,error:'VOICE_CONFLICT'});
  }
  return json_({ok:true,note_id:key,category:category,sha256:request.sha256,size:bytes.length});
}


function voiceKey_(sha256, category, photoId, transcript) {
  const text = sha256 + '\n' + category + '\n' + photoId + '\n' + transcript;
  // Do not rely on the platform's default encoding for Ukrainian/emoji text.
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(b => ('0' + ((b + 256) % 256).toString(16)).slice(-2)).join('').slice(0,24);
}

function testVoiceProtocol() {
  const key = voiceKey_('a'.repeat(64), 'memories', '', 'Мій спогад — синя чашка 🇺🇦');
  if (key !== '002d773446f06f6a5737d4e0') throw new Error('VOICE_UTF8_SELF_TEST_FAILED');
  console.log('Voice UTF-8 protocol self-test passed.');
}

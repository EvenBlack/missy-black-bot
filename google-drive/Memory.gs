/** Missy memory storage. Run setupMemory once, then deploy as a web app. */
const MISSY_FOLDER_ID = '1rNoUkxNvYh_r8_ouYxOlnIpJ4OG1mHvC';

function setupMemory() {
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
    if (!['get', 'put', 'archive_photo', 'get_photo', 'list_photos'].includes(request.action)) return json_({ok: false, error: 'INVALID_ACTION'});
    const fileId = props.getProperty('MISSY_MEMORY_FILE_ID');
    if (!fileId) return json_({ok: false, error: 'SETUP_REQUIRED'});
    lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) return json_({ok: false, error: 'BUSY'});
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
    if (request.action === 'get') return json_({ok: true, revision: store.revision, data: store.data, backup_ready: Boolean(backupId), photo_archive_ready: Boolean(props.getProperty('MISSY_PHOTO_FOLDER_ID')), recovered: recovered});
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
        const text = String(meta.caption || '') + ' ' + String(meta.filename || '') + ' ' + String(meta.created || '');
        if (query.every(word => text.toLowerCase().includes(word))) {
          photos.push({archive_id:meta.archive_id, caption:meta.caption, filename:meta.filename, created:meta.created, original:meta.original});
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
    if (metaFile) metaFile.setContent(JSON.stringify(meta));
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
  return json_({ok:true,content_b64:Utilities.base64Encode(bytes),sha256:meta.sha256,filename:meta.filename,caption:meta.caption});
}

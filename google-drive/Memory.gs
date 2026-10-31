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
  if (store.data !== null && (typeof store.data !== 'object' || Array.isArray(store.data))) {
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
    if (!raw || raw.length > 524288) return json_({ok: false, error: 'INVALID_REQUEST'});
    const request = JSON.parse(raw);
    const props = PropertiesService.getScriptProperties();
    if (!sameSecret_(request.secret, props.getProperty('MISSY_MEMORY_SECRET'))) {
      return json_({ok: false, error: 'UNAUTHORIZED'});
    }
    if (!['get', 'put'].includes(request.action)) return json_({ok: false, error: 'INVALID_ACTION'});
    const fileId = props.getProperty('MISSY_MEMORY_FILE_ID');
    if (!fileId) return json_({ok: false, error: 'SETUP_REQUIRED'});
    lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) return json_({ok: false, error: 'BUSY'});
    const file = DriveApp.getFileById(fileId);
    const store = readStore_(file);
    if (request.action === 'get') return json_({ok: true, revision: store.revision, data: store.data});
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
    file.setContent(serialized);
    return json_({ok: true, revision: next.revision});
  } catch (error) {
    // Internal exceptions may contain Drive paths or IDs; do not return/log payloads.
    return json_({ok: false, error: 'STORAGE_ERROR'});
  } finally {
    if (lock && lock.hasLock()) lock.releaseLock();
  }
}

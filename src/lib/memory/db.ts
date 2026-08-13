// Minimal IndexedDB wrapper for the local-first memory system.
// Four stores: conversations / messages / user_memory / missions.
// No external deps — keeps the bundle lean and works fully offline.

export const DB_NAME = "dtsv-memory";
export const DB_VERSION = 1;

export type StoreName = "conversations" | "messages" | "user_memory" | "missions";

let dbPromise: Promise<IDBDatabase> | null = null;

export function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("conversations")) {
        const s = db.createObjectStore("conversations", { keyPath: "id" });
        s.createIndex("updatedAt", "updatedAt");
      }
      if (!db.objectStoreNames.contains("messages")) {
        const s = db.createObjectStore("messages", { keyPath: "id" });
        s.createIndex("conversationId", "conversationId");
      }
      if (!db.objectStoreNames.contains("user_memory")) {
        const s = db.createObjectStore("user_memory", { keyPath: "id" });
        s.createIndex("kind", "kind");
        s.createIndex("status", "status");
      }
      if (!db.objectStoreNames.contains("missions")) {
        const s = db.createObjectStore("missions", { keyPath: "id" });
        s.createIndex("ts", "ts");
        s.createIndex("type", "type");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

function tx<T>(store: StoreName, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const t = db.transaction(store, mode);
        const req = fn(t.objectStore(store));
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }),
  );
}

export const idbGetAll = <T>(store: StoreName): Promise<T[]> =>
  tx<T[]>(store, "readonly", (s) => s.getAll() as IDBRequest<T[]>);

export const idbGet = <T>(store: StoreName, key: string): Promise<T | undefined> =>
  tx<T | undefined>(store, "readonly", (s) => s.get(key) as IDBRequest<T | undefined>);

export const idbPut = <T>(store: StoreName, value: T): Promise<IDBValidKey> =>
  tx<IDBValidKey>(store, "readwrite", (s) => s.put(value as any));

export const idbDelete = (store: StoreName, key: string): Promise<undefined> =>
  tx<undefined>(store, "readwrite", (s) => s.delete(key) as IDBRequest<undefined>);

export const idbClear = (store: StoreName): Promise<undefined> =>
  tx<undefined>(store, "readwrite", (s) => s.clear() as IDBRequest<undefined>);

export async function idbBulkPut<T>(store: StoreName, values: T[]): Promise<void> {
  if (!values.length) return;
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const t = db.transaction(store, "readwrite");
    const os = t.objectStore(store);
    for (const v of values) os.put(v as any);
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
  });
}

export async function idbDeleteByIndex(store: StoreName, index: string, value: IDBValidKey): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const t = db.transaction(store, "readwrite");
    const os = t.objectStore(store);
    const cursorReq = os.index(index).openCursor(IDBKeyRange.only(value));
    cursorReq.onsuccess = () => {
      const cur = cursorReq.result;
      if (cur) {
        cur.delete();
        cur.continue();
      }
    };
    t.oncomplete = () => resolve();
    t.onerror = () => reject(t.error);
  });
}

export async function idbGetByIndex<T>(store: StoreName, index: string, value: IDBValidKey): Promise<T[]> {
  const db = await openDb();
  return new Promise<T[]>((resolve, reject) => {
    const t = db.transaction(store, "readonly");
    const req = t.objectStore(store).index(index).getAll(IDBKeyRange.only(value));
    req.onsuccess = () => resolve(req.result as T[]);
    req.onerror = () => reject(req.error);
  });
}

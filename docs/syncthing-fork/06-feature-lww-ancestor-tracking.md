# F5 — LWW + Ancestor Tracking

> **Muc tieu:** Thay the co che xu ly conflict hien tai cua Syncthing (tao file `.sync-conflict-*`) bang co che Last-Writer-Wins ket hop Ancestor Tracking. Khi ca hai phia cung thay doi mot file, phia co `mtime` moi nhat se thang — khong tao conflict file. Ancestor (trang thai dong thuan cuoi cung) duoc luu lai de phan biet chinh xac "mot phia thay doi" vs "ca hai phia thay doi".

---

## Phase 1 — Plan

### 1.1 Van de hien tai

Syncthing hien tai xu ly conflict nhu sau (trong `lib/model/folder_sendrecv.go`):

- Su dung **version vector** (`lib/protocol/vector.go`) de phat hien thay doi dong thoi
- Moi vector chua `[]Counter{ID ShortID, Value uint64}`, ham `Compare()` tra ve `Equal | Greater | Lesser | Concurrent`
- Khi phat hien `Concurrent`: file cu hon duoc doi ten thanh `.sync-conflict-<date>-<id>.<ext>`
- Phien ban thang (device ID cao nhat break tie) tro thanh ban chinh thuc
- Day la LWW voi conflict copy — **khong** merge noi dung

**Van de:**
- Conflict file gay roi cho nguoi dung — ho khong biet file nao la "dung"
- Khong co khai niem "ancestor" nen khong phan biet duoc truong hop chi mot phia thay doi vs ca hai phia thay doi
- Khong phu hop voi use case cua chung ta: sync don gian, sach se, khong conflict file

### 1.2 Thiet ke moi: LWW + Ancestor

**Nguyen tac cot loi:**

```
Ancestor = trang thai dong thuan cuoi cung giua hai phia (alpha va beta)

Reconcile(ancestor, alpha, beta):
  1. alpha == beta == ancestor  → khong lam gi (da dong bo)
  2. alpha != ancestor, beta == ancestor  → truyen alpha sang beta
  3. alpha == ancestor, beta != ancestor  → truyen beta sang alpha
  4. alpha != ancestor, beta != ancestor:
     a. alpha == beta  → da giong nhau, chi cap nhat ancestor
     b. alpha != beta  → mtime moi nhat thang, ghi de phia con lai
  5. Mot phia xoa + phia kia sua  → phia sua thang (giu du lieu)
  6. Ca hai phia xoa  → dong y xoa, xoa ancestor

Sau khi sync thanh cong → cap nhat ancestor = trang thai dong thuan moi
```

**Khong tao conflict file** — day la diem khac biet chinh so voi Syncthing goc.

### 1.3 Cau truc du lieu Ancestor

```protobuf
// proto/ext.proto

message AncestorEntry {
  string  path          = 1;  // duong dan tuong doi cua file
  int64   mtime_unix    = 2;  // modification time (Unix seconds)
  int64   size          = 3;  // kich thuoc file (bytes)
  bytes   block_hash    = 4;  // SHA-256 cua noi dung (hoac hash cua block list)
  bool    deleted       = 5;  // co bi xoa khong
  int64   updated_at    = 6;  // thoi diem cap nhat ancestor entry nay
}
```

### 1.4 Thay doi co so du lieu

Them bang `ancestor_entries` trong SQLite (lib/db):

```sql
CREATE TABLE IF NOT EXISTS ancestor_entries (
    folder_id   TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    mtime_unix  INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    block_hash  BLOB    NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (folder_id, path)
);
```

### 1.5 Cac file can thay doi

| File | Hanh dong | Mo ta |
|------|-----------|-------|
| `proto/ext.proto` | Them | Dinh nghia protobuf cho `AncestorEntry` |
| `lib/sync/ancestor.go` | Them | Interface va struct de quan ly ancestor entries |
| `lib/sync/reconcile.go` | Them | Logic reconcile voi ancestor comparison |
| `lib/sync/reconcile_test.go` | Them | Unit test cho moi truong hop reconcile |
| `lib/db/ancestor.go` | Them | CRUD operations cho bang `ancestor_entries` |
| `lib/db/migrations.go` | Sua | Them migration tao bang `ancestor_entries` |
| `lib/model/folder_sendrecv.go` | Sua | Thay `handleConflict` bang reconciler moi |
| `lib/config/folderconfiguration.go` | Sua | Them option `UseLWWReconciler bool` |

### 1.6 Luu do xu ly

```
File thay doi duoc phat hien
        |
        v
Lay ancestor entry tu DB (theo folder_id + path)
        |
        v
So sanh alpha vs ancestor, beta vs ancestor
        |
        +---> Chi alpha thay doi ---> Truyen alpha sang beta
        |                             Cap nhat ancestor = alpha
        |
        +---> Chi beta thay doi ----> Truyen beta sang alpha
        |                             Cap nhat ancestor = beta
        |
        +---> Ca hai thay doi ------> So sanh mtime
        |         |                      |
        |         +-> alpha moi hon ---> Truyen alpha sang beta
        |         |                      Cap nhat ancestor = alpha
        |         |
        |         +-> beta moi hon ----> Truyen beta sang alpha
        |                                Cap nhat ancestor = beta
        |
        +---> Mot xoa, mot sua -----> Phia sua thang
        |                             Cap nhat ancestor = phia sua
        |
        +---> Ca hai xoa -----------> Xoa ancestor entry
        |
        +---> Khong thay doi -------> Khong lam gi
```

---

## Phase 2 — Code

### 2.1 Protobuf definition

```protobuf
// proto/ext.proto

syntax = "proto3";
package syncfork.protocol;
option go_package = "github.com/our-fork/syncthing/lib/protocol";

message AncestorEntry {
  string path          = 1;
  int64  mtime_unix    = 2;
  int64  size          = 3;
  bytes  block_hash    = 4;
  bool   deleted       = 5;
  int64  updated_at    = 6;
}
```

### 2.2 Ancestor Store (lib/db/ancestor.go)

```go
package db

import (
	"database/sql"
	"time"

	"github.com/our-fork/syncthing/lib/protocol"
)

// AncestorStore quan ly cac ancestor entry trong SQLite.
type AncestorStore struct {
	db *sql.DB
}

func NewAncestorStore(db *sql.DB) *AncestorStore {
	return &AncestorStore{db: db}
}

// Get tra ve ancestor entry cho mot file trong mot folder.
// Tra ve nil, nil neu chua co ancestor (file moi).
func (s *AncestorStore) Get(folderID, path string) (*protocol.AncestorEntry, error) {
	row := s.db.QueryRow(
		`SELECT path, mtime_unix, size, block_hash, deleted, updated_at
		 FROM ancestor_entries
		 WHERE folder_id = ? AND path = ?`,
		folderID, path,
	)

	var entry protocol.AncestorEntry
	err := row.Scan(
		&entry.Path,
		&entry.MtimeUnix,
		&entry.Size,
		&entry.BlockHash,
		&entry.Deleted,
		&entry.UpdatedAt,
	)
	if err == sql.ErrNoRows {
		return nil, nil // chua co ancestor — file moi
	}
	if err != nil {
		return nil, err
	}
	return &entry, nil
}

// Put luu hoac cap nhat ancestor entry sau khi sync thanh cong.
func (s *AncestorStore) Put(folderID string, entry *protocol.AncestorEntry) error {
	entry.UpdatedAt = time.Now().Unix()

	_, err := s.db.Exec(
		`INSERT INTO ancestor_entries (folder_id, path, mtime_unix, size, block_hash, deleted, updated_at)
		 VALUES (?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT(folder_id, path) DO UPDATE SET
		   mtime_unix = excluded.mtime_unix,
		   size       = excluded.size,
		   block_hash = excluded.block_hash,
		   deleted    = excluded.deleted,
		   updated_at = excluded.updated_at`,
		folderID,
		entry.Path,
		entry.MtimeUnix,
		entry.Size,
		entry.BlockHash,
		entry.Deleted,
		entry.UpdatedAt,
	)
	return err
}

// Delete xoa ancestor entry (khi ca hai phia dong y xoa file).
func (s *AncestorStore) Delete(folderID, path string) error {
	_, err := s.db.Exec(
		`DELETE FROM ancestor_entries WHERE folder_id = ? AND path = ?`,
		folderID, path,
	)
	return err
}

// DeleteAll xoa tat ca ancestor entries cua mot folder (khi reset folder).
func (s *AncestorStore) DeleteAll(folderID string) error {
	_, err := s.db.Exec(
		`DELETE FROM ancestor_entries WHERE folder_id = ?`,
		folderID,
	)
	return err
}
```

### 2.3 DB Migration (lib/db/migrations.go)

```go
// Them vao danh sach migrations hien co:

var migrations = []migration{
	// ... cac migration cu ...
	{
		name: "add_ancestor_entries_table",
		fn: func(tx *sql.Tx) error {
			_, err := tx.Exec(`
				CREATE TABLE IF NOT EXISTS ancestor_entries (
					folder_id   TEXT    NOT NULL,
					path        TEXT    NOT NULL,
					mtime_unix  INTEGER NOT NULL,
					size        INTEGER NOT NULL,
					block_hash  BLOB    NOT NULL,
					deleted     INTEGER NOT NULL DEFAULT 0,
					updated_at  INTEGER NOT NULL,
					PRIMARY KEY (folder_id, path)
				)
			`)
			return err
		},
	},
}
```

### 2.4 File Entry abstraction (lib/sync/reconcile.go)

```go
package sync

import (
	"bytes"
	"fmt"
	"time"

	"github.com/our-fork/syncthing/lib/db"
	"github.com/our-fork/syncthing/lib/protocol"
)

// FileEntry dai dien cho trang thai cua mot file tren mot phia (alpha hoac beta).
type FileEntry struct {
	Path      string
	MtimeUnix int64
	Size      int64
	BlockHash []byte
	Deleted   bool
}

// ToAncestor chuyen doi FileEntry thanh AncestorEntry de luu vao DB.
func (f *FileEntry) ToAncestor() *protocol.AncestorEntry {
	return &protocol.AncestorEntry{
		Path:      f.Path,
		MtimeUnix: f.MtimeUnix,
		Size:      f.Size,
		BlockHash: f.BlockHash,
		Deleted:   f.Deleted,
	}
}

// matchesAncestor kiem tra xem file co giong ancestor khong.
// Neu ancestor == nil (file moi), luon tra ve false.
func (f *FileEntry) matchesAncestor(anc *protocol.AncestorEntry) bool {
	if anc == nil {
		return false
	}
	if f.Deleted && anc.Deleted {
		return true
	}
	if f.Deleted != anc.Deleted {
		return false
	}
	return f.MtimeUnix == anc.MtimeUnix &&
		f.Size == anc.Size &&
		bytes.Equal(f.BlockHash, anc.BlockHash)
}

// Action mo ta hanh dong can thuc hien sau khi reconcile.
type Action int

const (
	ActionNone         Action = iota // khong can lam gi
	ActionPropAlpha                  // truyen alpha -> beta
	ActionPropBeta                   // truyen beta -> alpha
	ActionDeleteBoth                 // ca hai dong y xoa
)

// ReconcileResult chua ket qua cua viec reconcile mot file.
type ReconcileResult struct {
	Path    string
	Action  Action
	Winner  *FileEntry // file entry se tro thanh trang thai moi (nil neu ActionDeleteBoth)
	Reason  string     // giai thich ngan gon de log
}

// Reconciler thuc hien reconcile giua alpha va beta dua tren ancestor.
type Reconciler struct {
	store *db.AncestorStore
}

func NewReconciler(store *db.AncestorStore) *Reconciler {
	return &Reconciler{store: store}
}

// Reconcile so sanh alpha va beta voi ancestor de quyet dinh hanh dong.
//
// Quy tac:
//   - Chi alpha thay doi   → truyen alpha
//   - Chi beta thay doi    → truyen beta
//   - Ca hai thay doi      → mtime moi nhat thang (LWW)
//   - Mot xoa + mot sua    → phia sua thang
//   - Ca hai xoa           → dong y xoa
//   - Khong thay doi       → bo qua
func (r *Reconciler) Reconcile(folderID string, alpha, beta *FileEntry) (*ReconcileResult, error) {
	ancestor, err := r.store.Get(folderID, alpha.Path)
	if err != nil {
		return nil, fmt.Errorf("lay ancestor that bai cho %q: %w", alpha.Path, err)
	}

	result := &ReconcileResult{Path: alpha.Path}

	alphaMatchesAnc := alpha.matchesAncestor(ancestor)
	betaMatchesAnc := beta.matchesAncestor(ancestor)

	switch {
	// Truong hop 1: Ca hai giong nhau va giong ancestor → khong lam gi
	case alphaMatchesAnc && betaMatchesAnc:
		result.Action = ActionNone
		result.Reason = "ca hai phia khong thay doi"

	// Truong hop 2: Ca hai giong nhau nhung khac ancestor → cap nhat ancestor
	case alpha.equalTo(beta):
		result.Action = ActionNone
		result.Winner = alpha
		result.Reason = "ca hai phia da giong nhau, cap nhat ancestor"

	// Truong hop 3: Chi alpha thay doi
	case !alphaMatchesAnc && betaMatchesAnc:
		result.Action = ActionPropAlpha
		result.Winner = alpha
		result.Reason = "chi alpha thay doi"

	// Truong hop 4: Chi beta thay doi
	case alphaMatchesAnc && !betaMatchesAnc:
		result.Action = ActionPropBeta
		result.Winner = beta
		result.Reason = "chi beta thay doi"

	// Truong hop 5: Ca hai thay doi — can xu ly conflict
	default:
		result = r.resolveConflict(alpha, beta)
	}

	return result, nil
}

// resolveConflict xu ly khi ca hai phia deu thay doi so voi ancestor.
func (r *Reconciler) resolveConflict(alpha, beta *FileEntry) *ReconcileResult {
	result := &ReconcileResult{Path: alpha.Path}

	switch {
	// Ca hai xoa → dong y xoa
	case alpha.Deleted && beta.Deleted:
		result.Action = ActionDeleteBoth
		result.Reason = "ca hai phia xoa"

	// Alpha xoa, beta sua → beta thang (giu du lieu)
	case alpha.Deleted && !beta.Deleted:
		result.Action = ActionPropBeta
		result.Winner = beta
		result.Reason = "alpha xoa nhung beta sua — giu du lieu"

	// Alpha sua, beta xoa → alpha thang (giu du lieu)
	case !alpha.Deleted && beta.Deleted:
		result.Action = ActionPropAlpha
		result.Winner = alpha
		result.Reason = "beta xoa nhung alpha sua — giu du lieu"

	// Ca hai sua → mtime moi nhat thang (LWW)
	default:
		if alpha.MtimeUnix >= beta.MtimeUnix {
			result.Action = ActionPropAlpha
			result.Winner = alpha
			result.Reason = fmt.Sprintf("LWW: alpha moi hon (alpha=%d, beta=%d)",
				alpha.MtimeUnix, beta.MtimeUnix)
		} else {
			result.Action = ActionPropBeta
			result.Winner = beta
			result.Reason = fmt.Sprintf("LWW: beta moi hon (alpha=%d, beta=%d)",
				alpha.MtimeUnix, beta.MtimeUnix)
		}
	}

	return result
}

// CommitAncestor cap nhat ancestor entry sau khi sync thanh cong.
func (r *Reconciler) CommitAncestor(folderID string, result *ReconcileResult) error {
	switch result.Action {
	case ActionDeleteBoth:
		return r.store.Delete(folderID, result.Path)
	case ActionNone:
		if result.Winner != nil {
			return r.store.Put(folderID, result.Winner.ToAncestor())
		}
		return nil
	default:
		if result.Winner != nil {
			return r.store.Put(folderID, result.Winner.ToAncestor())
		}
		return nil
	}
}

// equalTo kiem tra hai FileEntry co giong nhau khong (khong tinh path).
func (f *FileEntry) equalTo(other *FileEntry) bool {
	if f.Deleted && other.Deleted {
		return true
	}
	if f.Deleted != other.Deleted {
		return false
	}
	return f.MtimeUnix == other.MtimeUnix &&
		f.Size == other.Size &&
		bytes.Equal(f.BlockHash, other.BlockHash)
}
```

### 2.5 Tich hop vao folder_sendrecv.go

```go
// lib/model/folder_sendrecv.go
// Thay doi trong ham copierRoutine hoac pullFile

// TRUOC (code cu):
//
// if f.IsInvalid() {
//     // skip
// } else if cf, ok := snap.GetGlobal(f.Name); ok && cf.IsEquivalent(f, ...) {
//     // ...
// } else {
//     s.handleConflict(f)  // <-- tao .sync-conflict file
// }

// SAU (code moi):
func (f *sendReceiveFolder) reconcileAndSync(
	folderID string,
	alphaFile, betaFile *FileEntry,
) error {
	result, err := f.reconciler.Reconcile(folderID, alphaFile, betaFile)
	if err != nil {
		return fmt.Errorf("reconcile that bai: %w", err)
	}

	l.Debugf("Reconcile %s: action=%v, reason=%s", result.Path, result.Action, result.Reason)

	switch result.Action {
	case sync.ActionNone:
		// Khong can lam gi — nhung van commit ancestor neu can
	case sync.ActionPropAlpha:
		// Tai noi dung tu alpha va ghi vao beta
		if err := f.applyFileChange(result.Winner); err != nil {
			return err
		}
	case sync.ActionPropBeta:
		// Tai noi dung tu beta va ghi vao alpha
		if err := f.applyFileChange(result.Winner); err != nil {
			return err
		}
	case sync.ActionDeleteBoth:
		// Xoa file tren ca hai phia (da dong y)
		if err := f.deleteFile(result.Path); err != nil {
			return err
		}
	}

	// Chi commit ancestor sau khi hanh dong thanh cong
	if err := f.reconciler.CommitAncestor(folderID, result); err != nil {
		l.Warnf("Cap nhat ancestor that bai cho %s: %v", result.Path, err)
		// Khong return error — sync van thanh cong, ancestor se duoc
		// cap nhat lan sau
	}

	return nil
}

// handleConflict (ham cu) van giu lai nhung khong duoc goi nua.
// Dat deprecated tag de xoa trong phien ban sau.
//
// Deprecated: Su dung reconcileAndSync thay the.
func (f *sendReceiveFolder) handleConflict(file protocol.FileInfo) {
	// ... code cu giu nguyen de rollback ...
}
```

### 2.6 Config option (lib/config/folderconfiguration.go)

```go
// Them vao struct FolderConfiguration:

type FolderConfiguration struct {
	// ... cac field hien co ...

	// UseLWWReconciler bat co che LWW + Ancestor thay cho conflict file.
	// Mac dinh: false (su dung conflict file nhu cu).
	// Khi bat: khong tao .sync-conflict file, mtime moi nhat thang.
	UseLWWReconciler bool `xml:"useLWWReconciler" json:"useLWWReconciler" default:"false"`
}
```

---

## Phase 3 — Review Checklist

### 3.1 Correctness

- [ ] **Reconcile logic bao phu du 6 truong hop:** khong thay doi, chi alpha, chi beta, ca hai giong, ca hai khac (LWW), mot xoa mot sua, ca hai xoa
- [ ] **Ancestor duoc cap nhat CHI SAU khi sync thanh cong** — neu sync fail, ancestor giu nguyen de retry dung
- [ ] **File moi (khong co ancestor):** ca alpha va beta deu khong match ancestor (nil) → roi vao nhanh conflict → LWW theo mtime
- [ ] **mtime bang nhau:** alpha thang (deterministic tie-break) — dam bao consistent giua cac node
- [ ] **Path comparison:** su dung duong dan tuong doi, da normalize (filepath.ToSlash)
- [ ] **Block hash comparison:** dung `bytes.Equal`, khong dung `==` tren slice

### 3.2 Data Integrity

- [ ] **Ancestor entry chi luu metadata** (mtime, size, hash) — KHONG luu noi dung file
- [ ] **SQL injection:** su dung parameterized query (`?` placeholder), khong noi chuoi
- [ ] **Transaction safety:** Put va Delete chay trong transaction cua SQLite
- [ ] **Khong mat du lieu:** truong hop mot xoa + mot sua → phia sua LUON thang

### 3.3 Performance

- [ ] **Index tren PRIMARY KEY (folder_id, path):** lookup la O(log n)
- [ ] **Batch operations:** khi reconcile nhieu file, su dung transaction de gom cac Put/Delete
- [ ] **Khong doc noi dung file de so sanh:** chi dung metadata (mtime, size, block_hash)
- [ ] **Lazy loading:** chi query ancestor khi can reconcile, khong load tat ca khi startup

### 3.4 Backward Compatibility

- [ ] **Feature flag `UseLWWReconciler`:** mac dinh OFF — hanh vi cu (conflict file) van la default
- [ ] **Ham `handleConflict` giu nguyen:** dat deprecated, khong xoa — de rollback
- [ ] **Migration khong pha du lieu cu:** `CREATE TABLE IF NOT EXISTS` — khong anh huong bang khac
- [ ] **Config cu van hop le:** them field moi voi default value — config cu parse binh thuong

### 3.5 Edge Cases

- [ ] **Folder moi (chua co ancestor nao):** moi file deu la "file moi" → LWW theo mtime
- [ ] **Reset folder:** goi `DeleteAll(folderID)` de xoa toan bo ancestor → bat dau lai tu dau
- [ ] **File rename:** coi nhu xoa file cu + tao file moi — xu ly dung qua ancestor
- [ ] **Symbolic link:** bo qua (khong track ancestor cho symlink)
- [ ] **File >4GB:** size la int64, ho tro den 9.2 exabytes
- [ ] **Unicode path:** SQLite ho tro UTF-8 native, khong can xu ly dac biet

---

## Phase 4 — Test

### 4.1 Unit Tests — Reconcile Logic

```go
// lib/sync/reconcile_test.go
package sync

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/our-fork/syncthing/lib/db"
)

// newTestReconciler tao Reconciler voi in-memory SQLite DB de test.
func newTestReconciler(t *testing.T) (*Reconciler, *db.AncestorStore) {
	t.Helper()
	sqlDB, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("mo SQLite that bai: %v", err)
	}
	t.Cleanup(func() { sqlDB.Close() })

	_, err = sqlDB.Exec(`
		CREATE TABLE ancestor_entries (
			folder_id   TEXT    NOT NULL,
			path        TEXT    NOT NULL,
			mtime_unix  INTEGER NOT NULL,
			size        INTEGER NOT NULL,
			block_hash  BLOB    NOT NULL,
			deleted     INTEGER NOT NULL DEFAULT 0,
			updated_at  INTEGER NOT NULL,
			PRIMARY KEY (folder_id, path)
		)
	`)
	if err != nil {
		t.Fatalf("tao bang that bai: %v", err)
	}

	store := db.NewAncestorStore(sqlDB)
	return NewReconciler(store), store
}

// makeEntry tao FileEntry de test.
func makeEntry(path string, mtime int64, size int64, hash []byte, deleted bool) *FileEntry {
	return &FileEntry{
		Path:      path,
		MtimeUnix: mtime,
		Size:      size,
		BlockHash: hash,
		Deleted:   deleted,
	}
}

// --- TEST: Khong co thay doi ---
// Kiem tra: khi alpha, beta, va ancestor deu giong nhau thi khong co hanh dong nao.
func TestReconcile_NoChange(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	// Thiet lap ancestor
	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	beta := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionNone {
		t.Errorf("mong doi ActionNone, nhan duoc %v", result.Action)
	}
}

// --- TEST: Chi alpha thay doi ---
// Kiem tra: khi chi alpha thay doi so voi ancestor, alpha duoc truyen sang beta.
func TestReconcile_OnlyAlphaChanged(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	// Ancestor = trang thai cu
	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	// Alpha da thay doi
	alpha := makeEntry("file.txt", 2000, 200, []byte("hash2"), false)
	// Beta van giong ancestor
	beta := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropAlpha {
		t.Errorf("mong doi ActionPropAlpha, nhan duoc %v", result.Action)
	}
	if result.Winner.MtimeUnix != 2000 {
		t.Errorf("winner phai la alpha (mtime=2000), nhan duoc mtime=%d", result.Winner.MtimeUnix)
	}
}

// --- TEST: Chi beta thay doi ---
// Kiem tra: khi chi beta thay doi so voi ancestor, beta duoc truyen sang alpha.
func TestReconcile_OnlyBetaChanged(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	beta := makeEntry("file.txt", 3000, 300, []byte("hash3"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropBeta {
		t.Errorf("mong doi ActionPropBeta, nhan duoc %v", result.Action)
	}
	if result.Winner.MtimeUnix != 3000 {
		t.Errorf("winner phai la beta (mtime=3000), nhan duoc mtime=%d", result.Winner.MtimeUnix)
	}
}

// --- TEST: Ca hai thay doi, alpha moi hon → LWW alpha thang ---
// Kiem tra: khi ca hai phia thay doi va alpha co mtime lon hon, alpha thang.
func TestReconcile_BothChanged_AlphaNewerWins(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 5000, 500, []byte("hash_alpha"), false)
	beta := makeEntry("file.txt", 3000, 300, []byte("hash_beta"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropAlpha {
		t.Errorf("mong doi ActionPropAlpha (LWW), nhan duoc %v", result.Action)
	}
	if result.Winner.MtimeUnix != 5000 {
		t.Errorf("winner phai la alpha (mtime=5000)")
	}
}

// --- TEST: Ca hai thay doi, beta moi hon → LWW beta thang ---
// Kiem tra: khi ca hai phia thay doi va beta co mtime lon hon, beta thang.
func TestReconcile_BothChanged_BetaNewerWins(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 3000, 300, []byte("hash_alpha"), false)
	beta := makeEntry("file.txt", 5000, 500, []byte("hash_beta"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropBeta {
		t.Errorf("mong doi ActionPropBeta (LWW), nhan duoc %v", result.Action)
	}
	if result.Winner.MtimeUnix != 5000 {
		t.Errorf("winner phai la beta (mtime=5000)")
	}
}

// --- TEST: mtime bang nhau → alpha thang (deterministic tie-break) ---
// Kiem tra: khi ca hai co cung mtime, alpha thang de dam bao ket qua nhat quan.
func TestReconcile_BothChanged_SameMtime_AlphaWins(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 4000, 400, []byte("hash_alpha"), false)
	beta := makeEntry("file.txt", 4000, 450, []byte("hash_beta"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropAlpha {
		t.Errorf("mong doi ActionPropAlpha (tie-break), nhan duoc %v", result.Action)
	}
}

// --- TEST: Alpha xoa, beta sua → beta thang (giu du lieu) ---
// Kiem tra: khi mot phia xoa va phia kia sua, phia sua luon thang de tranh mat du lieu.
func TestReconcile_AlphaDeleted_BetaModified(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 2000, 0, nil, true)    // da xoa
	beta := makeEntry("file.txt", 3000, 300, []byte("hash_beta"), false)  // da sua

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropBeta {
		t.Errorf("mong doi ActionPropBeta (giu du lieu), nhan duoc %v", result.Action)
	}
	if result.Winner.Deleted {
		t.Error("winner khong duoc la deleted")
	}
}

// --- TEST: Beta xoa, alpha sua → alpha thang (giu du lieu) ---
// Kiem tra: truong hop nguoc lai — alpha sua, beta xoa — alpha thang.
func TestReconcile_BetaDeleted_AlphaModified(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 3000, 300, []byte("hash_alpha"), false) // da sua
	beta := makeEntry("file.txt", 2000, 0, nil, true)   // da xoa

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionPropAlpha {
		t.Errorf("mong doi ActionPropAlpha (giu du lieu), nhan duoc %v", result.Action)
	}
}

// --- TEST: Ca hai xoa → dong y xoa, xoa ancestor ---
// Kiem tra: khi ca hai phia xoa cung file thi dong y xoa va xoa ancestor entry.
func TestReconcile_BothDeleted(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 2000, 0, nil, true)
	beta := makeEntry("file.txt", 2000, 0, nil, true)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionDeleteBoth {
		t.Errorf("mong doi ActionDeleteBoth, nhan duoc %v", result.Action)
	}

	// Commit va kiem tra ancestor da bi xoa
	err = rec.CommitAncestor(folder, result)
	if err != nil {
		t.Fatalf("commit ancestor loi: %v", err)
	}
	entry, err := store.Get(folder, "file.txt")
	if err != nil {
		t.Fatalf("get ancestor loi: %v", err)
	}
	if entry != nil {
		t.Error("ancestor phai bi xoa sau khi ca hai phia xoa")
	}
}

// --- TEST: File moi (khong co ancestor) → LWW ---
// Kiem tra: khi file chua co ancestor (file moi), ca hai phia deu "thay doi"
// so voi ancestor (nil), nen ap dung LWW theo mtime.
func TestReconcile_NewFile_NoAncestor(t *testing.T) {
	rec, _ := newTestReconciler(t)
	folder := "folder1"

	// Khong co ancestor cho file nay
	alpha := makeEntry("new.txt", 5000, 500, []byte("hash_alpha"), false)
	beta := makeEntry("new.txt", 3000, 300, []byte("hash_beta"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	// Ca hai khong match ancestor (nil) → conflict → LWW
	if result.Action != ActionPropAlpha {
		t.Errorf("mong doi ActionPropAlpha (LWW, alpha moi hon), nhan duoc %v", result.Action)
	}
}

// --- TEST: Ca hai thay doi giong nhau → chi cap nhat ancestor ---
// Kiem tra: khi ca hai phia thay doi giong het nhau (cung hash, cung mtime),
// khong can truyen du lieu, chi cap nhat ancestor.
func TestReconcile_BothChangedIdentically(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 2000, 200, []byte("hash2"), false)
	beta := makeEntry("file.txt", 2000, 200, []byte("hash2"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}
	if result.Action != ActionNone {
		t.Errorf("mong doi ActionNone (da giong nhau), nhan duoc %v", result.Action)
	}
	if result.Winner == nil {
		t.Error("winner phai khong nil de cap nhat ancestor")
	}
}

// --- TEST: CommitAncestor cap nhat DB dung ---
// Kiem tra: sau khi commit, ancestor trong DB phai phan anh trang thai moi.
func TestCommitAncestor_UpdatesDB(t *testing.T) {
	rec, store := newTestReconciler(t)
	folder := "folder1"

	anc := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)
	store.Put(folder, anc.ToAncestor())

	alpha := makeEntry("file.txt", 5000, 500, []byte("hash_new"), false)
	beta := makeEntry("file.txt", 1000, 100, []byte("hash1"), false)

	result, err := rec.Reconcile(folder, alpha, beta)
	if err != nil {
		t.Fatalf("reconcile loi: %v", err)
	}

	err = rec.CommitAncestor(folder, result)
	if err != nil {
		t.Fatalf("commit loi: %v", err)
	}

	// Kiem tra ancestor da duoc cap nhat
	updated, err := store.Get(folder, "file.txt")
	if err != nil {
		t.Fatalf("get ancestor loi: %v", err)
	}
	if updated == nil {
		t.Fatal("ancestor khong duoc tim thay sau commit")
	}
	if updated.MtimeUnix != 5000 {
		t.Errorf("ancestor mtime phai la 5000, nhan duoc %d", updated.MtimeUnix)
	}
}
```

### 4.2 Unit Tests — Ancestor Store

```go
// lib/db/ancestor_test.go
package db

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3"
	"github.com/our-fork/syncthing/lib/protocol"
)

func newTestStore(t *testing.T) *AncestorStore {
	t.Helper()
	sqlDB, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("mo SQLite that bai: %v", err)
	}
	t.Cleanup(func() { sqlDB.Close() })

	_, err = sqlDB.Exec(`
		CREATE TABLE ancestor_entries (
			folder_id TEXT NOT NULL, path TEXT NOT NULL,
			mtime_unix INTEGER NOT NULL, size INTEGER NOT NULL,
			block_hash BLOB NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
			updated_at INTEGER NOT NULL, PRIMARY KEY (folder_id, path)
		)
	`)
	if err != nil {
		t.Fatalf("tao bang that bai: %v", err)
	}
	return NewAncestorStore(sqlDB)
}

// --- TEST: Get tra ve nil cho entry chua ton tai ---
// Kiem tra: khi chua co ancestor nao, Get tra ve nil khong loi.
func TestAncestorStore_Get_NotFound(t *testing.T) {
	store := newTestStore(t)
	entry, err := store.Get("folder1", "nonexistent.txt")
	if err != nil {
		t.Fatalf("loi khong mong doi: %v", err)
	}
	if entry != nil {
		t.Error("mong doi nil cho entry chua ton tai")
	}
}

// --- TEST: Put roi Get tra ve dung du lieu ---
// Kiem tra: luu ancestor roi doc lai phai tra ve dung metadata.
func TestAncestorStore_PutThenGet(t *testing.T) {
	store := newTestStore(t)
	entry := &protocol.AncestorEntry{
		Path:      "docs/readme.md",
		MtimeUnix: 1700000000,
		Size:      1024,
		BlockHash: []byte("abc123hash"),
		Deleted:   false,
	}

	err := store.Put("folder1", entry)
	if err != nil {
		t.Fatalf("put loi: %v", err)
	}

	got, err := store.Get("folder1", "docs/readme.md")
	if err != nil {
		t.Fatalf("get loi: %v", err)
	}
	if got == nil {
		t.Fatal("mong doi entry, nhan duoc nil")
	}
	if got.MtimeUnix != 1700000000 {
		t.Errorf("mtime sai: mong doi 1700000000, nhan duoc %d", got.MtimeUnix)
	}
	if got.Size != 1024 {
		t.Errorf("size sai: mong doi 1024, nhan duoc %d", got.Size)
	}
}

// --- TEST: Put ghi de entry cu (upsert) ---
// Kiem tra: goi Put hai lan voi cung key phai cap nhat gia tri, khong duplicate.
func TestAncestorStore_Put_Upsert(t *testing.T) {
	store := newTestStore(t)

	entry1 := &protocol.AncestorEntry{
		Path: "file.txt", MtimeUnix: 1000, Size: 100, BlockHash: []byte("old"),
	}
	store.Put("folder1", entry1)

	entry2 := &protocol.AncestorEntry{
		Path: "file.txt", MtimeUnix: 2000, Size: 200, BlockHash: []byte("new"),
	}
	store.Put("folder1", entry2)

	got, _ := store.Get("folder1", "file.txt")
	if got.MtimeUnix != 2000 {
		t.Errorf("mong doi mtime=2000 sau upsert, nhan duoc %d", got.MtimeUnix)
	}
}

// --- TEST: Delete xoa entry ---
// Kiem tra: sau khi Delete, Get phai tra ve nil.
func TestAncestorStore_Delete(t *testing.T) {
	store := newTestStore(t)

	entry := &protocol.AncestorEntry{
		Path: "file.txt", MtimeUnix: 1000, Size: 100, BlockHash: []byte("hash"),
	}
	store.Put("folder1", entry)
	store.Delete("folder1", "file.txt")

	got, _ := store.Get("folder1", "file.txt")
	if got != nil {
		t.Error("entry phai bi xoa")
	}
}

// --- TEST: DeleteAll chi xoa entries cua folder chi dinh ---
// Kiem tra: DeleteAll khong anh huong entries cua folder khac.
func TestAncestorStore_DeleteAll_ScopedToFolder(t *testing.T) {
	store := newTestStore(t)

	e1 := &protocol.AncestorEntry{Path: "a.txt", MtimeUnix: 1, Size: 1, BlockHash: []byte("1")}
	e2 := &protocol.AncestorEntry{Path: "b.txt", MtimeUnix: 2, Size: 2, BlockHash: []byte("2")}

	store.Put("folder1", e1)
	store.Put("folder2", e2)

	store.DeleteAll("folder1")

	got1, _ := store.Get("folder1", "a.txt")
	got2, _ := store.Get("folder2", "b.txt")

	if got1 != nil {
		t.Error("folder1 entry phai bi xoa")
	}
	if got2 == nil {
		t.Error("folder2 entry khong duoc bi anh huong")
	}
}
```

### 4.3 Integration Test Scenarios

| # | Kich ban | Mo ta | Ket qua mong doi |
|---|----------|-------|------------------|
| I1 | Sync lan dau | Hai node ket noi, folder trong, tao file tren node A | File xuat hien tren node B, ancestor duoc tao |
| I2 | Sua mot phia | Sua file tren node A, doi sync | Node B nhan ban moi, ancestor cap nhat |
| I3 | Sua hai phia, A moi hon | Sua file tren ca A va B, A co mtime lon hon | Ban cua A thang, B bi ghi de, khong co conflict file |
| I4 | Sua hai phia, B moi hon | Sua file tren ca A va B, B co mtime lon hon | Ban cua B thang, A bi ghi de, khong co conflict file |
| I5 | Xoa mot phia | Xoa file tren A, khong sua tren B | File bi xoa tren B, ancestor bi xoa |
| I6 | Xoa + sua | Xoa tren A, sua tren B | Ban cua B thang (giu du lieu), ancestor cap nhat |
| I7 | Xoa ca hai | Xoa tren ca A va B | Dong y xoa, ancestor bi xoa |
| I8 | Feature flag OFF | Tat `UseLWWReconciler` | Hanh vi cu: tao `.sync-conflict` file |
| I9 | Khoi dong lai giua sync | Node A crash giua luc sync | Ancestor chua duoc cap nhat → retry dung |
| I10 | Nhieu file dong thoi | Tao 100 file tren A, sync | Tat ca sync dung, moi file co ancestor |

### 4.4 Lenh chay test

```bash
# Unit tests
go test ./lib/sync/... -v -run TestReconcile
go test ./lib/db/... -v -run TestAncestorStore

# Voi race detector
go test ./lib/sync/... -race -count=3

# Integration tests (can 2 instance)
go test ./lib/integration/... -v -run TestLWW -timeout 120s

# Coverage
go test ./lib/sync/... -coverprofile=coverage.out
go tool cover -func=coverage.out | grep reconcile
# Muc tieu: >= 90% coverage cho reconcile.go
```

---

## Phase 5 — Deploy

### 5.1 Thu tu deploy

```
Buoc 1: Migration DB
  └── Chay migration them bang ancestor_entries
  └── Kiem tra bang da duoc tao: SELECT count(*) FROM ancestor_entries
  └── Rollback: DROP TABLE ancestor_entries

Buoc 2: Deploy code moi voi feature flag OFF
  └── Build binary moi
  └── Deploy len staging
  └── Kiem tra hanh vi cu van hoat dong (conflict file van duoc tao)

Buoc 3: Bat feature flag tren staging
  └── Set UseLWWReconciler = true tren 1 folder
  └── Chay test suite I1-I10
  └── Kiem tra: KHONG co .sync-conflict file duoc tao
  └── Kiem tra: ancestor_entries duoc populate dung

Buoc 4: Canary deployment (production)
  └── Bat tren 5% folders
  └── Monitor 24h:
      - So luong sync thanh cong / that bai
      - Kich thuoc bang ancestor_entries
      - Latency cua sync cycle
  └── Neu OK → tang len 25% → 50% → 100%

Buoc 5: Xoa code cu
  └── Chi sau khi 100% folders chay LWW on dinh >= 2 tuan
  └── Xoa handleConflict va cac ham lien quan
  └── Xoa feature flag (UseLWWReconciler luon la true)
```

### 5.2 Monitoring

| Metric | Nguong canh bao | Mo ta |
|--------|-----------------|-------|
| `sync_reconcile_total` | — | Tong so file duoc reconcile |
| `sync_reconcile_action{type="prop_alpha"}` | — | So file alpha thang |
| `sync_reconcile_action{type="prop_beta"}` | — | So file beta thang |
| `sync_reconcile_action{type="lww"}` | tang dot bien | So file dung LWW (conflict thuc su) |
| `sync_reconcile_errors` | > 0 | Loi khi reconcile |
| `ancestor_store_size_bytes` | > 1GB | Kich thuoc bang ancestor |
| `ancestor_commit_latency_ms` | p99 > 100ms | Thoi gian ghi ancestor |
| `sync_conflict_files_created` | > 0 khi flag ON | Khong duoc tao conflict file khi flag ON |

### 5.3 Log format

```
# Reconcile thanh cong
INFO reconcile path="file.txt" action=prop_alpha reason="chi alpha thay doi" folder="folder1"

# LWW conflict
INFO reconcile path="file.txt" action=prop_alpha reason="LWW: alpha moi hon (alpha=1700000500, beta=1700000300)" folder="folder1"

# Loi
ERROR reconcile path="file.txt" error="lay ancestor that bai: database locked" folder="folder1"

# Ancestor commit
DEBUG ancestor_commit path="file.txt" mtime=1700000500 size=2048 folder="folder1"
```

---

## Rollback Plan

### Trigger rollback khi

1. **Sync failure rate tang > 5%** so voi baseline truoc khi bat feature
2. **Mat du lieu:** bat ky bao cao nao ve file bi mat noi dung
3. **Performance:** sync cycle cham hon 2x so voi truoc
4. **DB corruption:** ancestor_entries bi loi hoac gay loi cac bang khac

### Cac buoc rollback

```
Buoc 1: Tat feature flag (NGAY LAP TUC, khong can deploy lai)
  └── Set UseLWWReconciler = false tren tat ca folders
  └── Thoi gian: < 1 phut (thay doi config, restart service)
  └── Ket qua: quay lai hanh vi cu (conflict file), code moi van ton tai nhung khong chay

Buoc 2: Kiem tra hanh vi cu hoat dong
  └── Tao conflict thu cong tren 2 node
  └── Kiem tra .sync-conflict file duoc tao
  └── Kiem tra khong co data loss

Buoc 3: Quyet dinh xu ly bang ancestor_entries
  └── Lua chon A: Giu lai (khong hai gi, chi ton dung luong)
  └── Lua chon B: Truncate (TRUNCATE TABLE ancestor_entries)
  └── Lua chon C: Drop table (chi khi quyet dinh bo feature hoan toan)
  └── KHONG drop table ngay — giu lai de debug

Buoc 4: Deploy lai binary cu (chi khi can)
  └── Chi can neu code moi gay loi khong lien quan den feature flag
  └── Revert commit, build, deploy
  └── Thoi gian: 15-30 phut
```

### Diem quan trong

- **Feature flag la tuyen phong thu dau tien** — tat flag la du de rollback trong hau het truong hop
- **Bang `ancestor_entries` doc lap** — khong anh huong cac bang khac khi xoa/truncate
- **Ham `handleConflict` cu van con** — code cu hoat dong binh thuong khi flag OFF
- **Khong can migration nguoc** — bang ancestor_entries khong pha gi khi ton tai ma khong duoc dung

---

*Tai lieu nay duoc tao ngay 2026-06-10. Cap nhat khi co thay doi thiet ke.*

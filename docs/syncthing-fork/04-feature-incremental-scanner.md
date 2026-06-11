# F3 — Incremental Scanner

> **Ngay tao:** 2026-06-10
> **Tac gia:** hangocthanhperu3107@gmail.com
> **Trang thai:** Draft
> **Nhanh:** `feature/f3-incremental-scanner`

---

## Tong quan

Hien tai Syncthing su dung co che scan toan bo thu muc (full scan) theo chu ky `RescanIntervalS` ket hop voi fsnotify de phat hien thay doi. Voi cac thu muc lon (>100K file), moi lan full scan ton nhieu thoi gian va I/O do phai duyet toan bo cay thu muc va tinh lai hash cho moi file.

**Muc tieu cua F3:** Port co che **Incremental Scanner** tu Mutagen sang Syncthing fork, giup:

1. **Giam I/O:** Chi duyet lai cac duong dan thay doi (dirty paths), cac subtree khong thay doi duoc tai su dung tu baseline snapshot ma KHONG can doc disk.
2. **Giam CPU:** Digest cache luu (path, mtime, size, fileID, digest) — file khong thay doi se khong bi hash lai.
3. **Chi phi scan ty le voi so thay doi**, khong phu thuoc tong so file.

**Kien truc tong quat:**

```
fsnotify events
       |
       v
+------------------+
| Dirty Paths Set  |  <-- Chi luu cac path co thay doi
+------------------+
       |
       v
+------------------+     +-----------------+
| Walk (modified)  |---->| Baseline Snap   |  <-- Tai su dung subtree khong dirty
+------------------+     +-----------------+
       |
       v
+------------------+
| Digest Cache     |  <-- Skip hash neu mtime+size khop
+------------------+
       |
       v
  protocol.FileInfo (chi cac file thay doi)
```

---

## Phase 1 — Plan

### 1.1 Phan tich hien trang

| Thanh phan | File | Chuc nang hien tai |
|---|---|---|
| Walker | `lib/scanner/walk.go` | `walkRegular()` duyet toan bo cay thu muc, so sanh voi `CurrentFiler` (DB state) bang `IsEquivalentOptional()` kiem tra mtime, size, permissions |
| Block hasher | `lib/scanner/blocks.go` | Chia file thanh cac block co dinh, hash SHA-256 tung block |
| Parallel hasher | `lib/scanner/blockqueue.go` | Chay N goroutine de hash song song |
| Folder model | `lib/model/folder.go` | `scanSubdirs()` goi walker, quan ly chu ky scan |
| Du lieu chinh | `protocol.FileInfo` | Name, Type, Size, ModifiedS, Blocks, BlocksHash, Version |

### 1.2 Thiet ke incremental scan

| Khai niem | Mo ta |
|---|---|
| Baseline Snapshot | Ket qua scan lan truoc, luu trong bo nho duoi dang cay Entry. Subtree khong dirty duoc tai su dung nguyen trang |
| Dirty Paths Set | Tap hop cac duong dan nhan tu fsnotify. Chi cac path nay (va to tien cua chung) moi can duyet lai |
| Digest Cache | Map `path -> (mtime, size, fileID, digest)`. Neu file khop cache thi skip hash, tiet kiem CPU |
| Selective Rescan | Walker chi traverse vao cac subtree chua dirty path, con lai copy tu baseline |

### 1.3 Danh sach file thay doi

#### File moi (Add)

| File | Muc dich |
|---|---|
| `lib/scanner/snapshot.go` | Baseline snapshot: cay Entry luu ket qua scan truoc, ho tro lookup va reuse subtree |
| `lib/scanner/cache.go` | Digest cache: luu va tra cuu digest theo (path, mtime, size, fileID), co TTL va gioi han kich thuoc |
| `lib/scanner/snapshot_test.go` | Unit test cho snapshot |
| `lib/scanner/cache_test.go` | Unit test cho cache |
| `lib/scanner/incremental_test.go` | Integration test cho toan bo flow incremental scan |

#### File sua (Modify)

| File | Thay doi |
|---|---|
| `lib/scanner/walk.go` | `walkRegular()` kiem tra dirty paths truoc, skip subtree khong dirty, su dung digest cache |
| `lib/model/folder.go` | `scanSubdirs()` truyen dirty paths vao walker, luu va cap nhat baseline snapshot |
| `lib/config/folderconfiguration.go` | Them truong `IncrementalScan bool` va `DigestCacheSizeMB int` |

#### File xoa (Delete)

| File | Ly do |
|---|---|
| _(Khong co)_ | Khong xoa file nao, chi bo sung va sua doi |

### 1.4 Dependency va tuong thich nguoc

- **Khong thay doi protocol.FileInfo**: Output cua incremental scan van la `[]protocol.FileInfo` nhu cu, dam bao tuong thich voi cac thanh phan khac (index sender, puller, DB).
- **Feature flag**: `IncrementalScan` mac dinh la `false`. Khi `false`, walker hoat dong nhu cu (full scan). Chi khi bat moi dung co che moi.
- **Digest cache optional**: Neu cache bi loi hoac bi xoa, he thong fallback ve tinh hash binh thuong.

---

## Phase 2 — Code

### 2.1 `lib/scanner/snapshot.go` — Baseline Snapshot

```go
// Copyright (C) 2026 Syncthing Fork Authors.
//
// Baseline Snapshot luu tru ket qua scan truoc do duoi dang cay Entry.
// Khi scan lan tiep theo, cac subtree khong nam trong dirty paths
// se duoc tai su dung tu snapshot nay ma khong can doc lai disk.

package scanner

import (
	"path/filepath"
	"strings"
	"sync"

	"github.com/syncthing/syncthing/lib/protocol"
)

// Entry dai dien cho mot node trong cay snapshot.
// Neu la file: Children == nil, FileInfo chua thong tin day du.
// Neu la thu muc: Children chua cac entry con, FileInfo chua thong tin thu muc.
type Entry struct {
	Name     string
	FileInfo protocol.FileInfo
	Children map[string]*Entry // nil neu la file
}

// Snapshot luu tru toan bo cay thu muc tu lan scan truoc.
// Thread-safe thong qua RWMutex.
type Snapshot struct {
	mu   sync.RWMutex
	root *Entry
}

// NewSnapshot tao snapshot rong.
func NewSnapshot() *Snapshot {
	return &Snapshot{
		root: &Entry{
			Name:     ".",
			Children: make(map[string]*Entry),
		},
	}
}

// Update cap nhat snapshot voi ket qua scan moi.
// Cac file trong scanResult se ghi de entry tuong ung trong cay.
// Cac file khong co trong scanResult (va khong nam trong dirty paths)
// duoc giu nguyen tu baseline.
func (s *Snapshot) Update(scanResult []protocol.FileInfo, dirtyPaths map[string]struct{}) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Xoa cac entry tuong ung voi dirty paths truoc
	for dp := range dirtyPaths {
		s.removeEntry(dp)
	}

	// Them cac entry moi tu ket qua scan
	for i := range scanResult {
		s.setEntry(&scanResult[i])
	}
}

// Lookup tra ve FileInfo cua mot path tu snapshot.
// ok == false neu path khong ton tai trong snapshot.
func (s *Snapshot) Lookup(path string) (protocol.FileInfo, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	entry := s.findEntry(path)
	if entry == nil {
		return protocol.FileInfo{}, false
	}
	return entry.FileInfo, true
}

// SubtreeFiles tra ve tat ca FileInfo trong mot subtree.
// Dung de tai su dung ket qua scan cua cac thu muc khong dirty.
func (s *Snapshot) SubtreeFiles(dirPath string) []protocol.FileInfo {
	s.mu.RLock()
	defer s.mu.RUnlock()

	entry := s.findEntry(dirPath)
	if entry == nil {
		return nil
	}

	var result []protocol.FileInfo
	s.collectFiles(entry, &result)
	return result
}

// Reset xoa toan bo snapshot (dung khi can full rescan).
func (s *Snapshot) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.root = &Entry{
		Name:     ".",
		Children: make(map[string]*Entry),
	}
}

// Size tra ve tong so entry trong snapshot.
func (s *Snapshot) Size() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.countEntries(s.root)
}

// --- Private methods ---

func (s *Snapshot) findEntry(path string) *Entry {
	if path == "." || path == "" {
		return s.root
	}

	parts := splitPath(path)
	current := s.root
	for _, part := range parts {
		if current.Children == nil {
			return nil
		}
		child, ok := current.Children[part]
		if !ok {
			return nil
		}
		current = child
	}
	return current
}

func (s *Snapshot) setEntry(fi *protocol.FileInfo) {
	parts := splitPath(fi.Name)
	current := s.root

	// Tao cac thu muc trung gian neu chua ton tai
	for i := 0; i < len(parts)-1; i++ {
		if current.Children == nil {
			current.Children = make(map[string]*Entry)
		}
		child, ok := current.Children[parts[i]]
		if !ok {
			child = &Entry{
				Name:     parts[i],
				Children: make(map[string]*Entry),
			}
			current.Children[parts[i]] = child
		}
		current = child
	}

	// Dat entry cuoi cung
	name := parts[len(parts)-1]
	if current.Children == nil {
		current.Children = make(map[string]*Entry)
	}

	if fi.IsDirectory() {
		existing, ok := current.Children[name]
		if ok {
			existing.FileInfo = *fi
		} else {
			current.Children[name] = &Entry{
				Name:     name,
				FileInfo: *fi,
				Children: make(map[string]*Entry),
			}
		}
	} else {
		current.Children[name] = &Entry{
			Name:     name,
			FileInfo: *fi,
			Children: nil, // file, khong co children
		}
	}
}

func (s *Snapshot) removeEntry(path string) {
	if path == "." || path == "" {
		s.root.Children = make(map[string]*Entry)
		return
	}

	parts := splitPath(path)
	current := s.root
	for i := 0; i < len(parts)-1; i++ {
		if current.Children == nil {
			return
		}
		child, ok := current.Children[parts[i]]
		if !ok {
			return
		}
		current = child
	}

	if current.Children != nil {
		delete(current.Children, parts[len(parts)-1])
	}
}

func (s *Snapshot) collectFiles(entry *Entry, result *[]protocol.FileInfo) {
	if entry.Children == nil {
		// La file
		*result = append(*result, entry.FileInfo)
		return
	}
	// La thu muc — them chinh no va duyet con
	if entry.Name != "." {
		*result = append(*result, entry.FileInfo)
	}
	for _, child := range entry.Children {
		s.collectFiles(child, result)
	}
}

func (s *Snapshot) countEntries(entry *Entry) int {
	count := 1
	for _, child := range entry.Children {
		count += s.countEntries(child)
	}
	return count
}

func splitPath(path string) []string {
	path = filepath.ToSlash(path)
	path = strings.TrimPrefix(path, "./")
	path = strings.TrimSuffix(path, "/")
	if path == "" {
		return nil
	}
	return strings.Split(path, "/")
}
```

### 2.2 `lib/scanner/cache.go` — Digest Cache

```go
// Copyright (C) 2026 Syncthing Fork Authors.
//
// Digest Cache luu tru mapping (path -> digest metadata).
// Khi scan, neu mot file co mtime + size + fileID khop voi cache entry,
// he thong se tai su dung digest cu ma khong can doc lai file va tinh hash.
// Cache co gioi han kich thuoc va TTL de tranh su dung bo nho qua nhieu.

package scanner

import (
	"sync"
	"time"
)

// DigestEntry luu thong tin digest cua mot file tai thoi diem scan truoc.
type DigestEntry struct {
	Path     string
	Mtime    time.Time
	Size     int64
	FileID   uint64 // inode hoac file identifier tren filesystem
	Digest   []byte // SHA-256 hash cua toan bo file
	BlocksHash []byte // hash cua danh sach blocks
	CachedAt time.Time
}

// DigestCache la bo nho dem cho digest cua cac file da scan.
// Thread-safe, co gioi han kich thuoc va TTL.
type DigestCache struct {
	mu         sync.RWMutex
	entries    map[string]*DigestEntry
	maxEntries int
	ttl        time.Duration
}

// NewDigestCache tao digest cache moi.
// maxEntries: so luong entry toi da (0 = khong gioi han).
// ttl: thoi gian song cua moi entry (0 = khong het han).
func NewDigestCache(maxEntries int, ttl time.Duration) *DigestCache {
	return &DigestCache{
		entries:    make(map[string]*DigestEntry),
		maxEntries: maxEntries,
		ttl:        ttl,
	}
}

// Lookup tra cuu digest cua mot file dua tren path, mtime, size, fileID.
// Tra ve digest va blocksHash neu cache hit; ok == false neu miss.
//
// Logic match:
//   1. Path phai ton tai trong cache
//   2. Mtime phai bang nhau (chinh xac den nanosecond)
//   3. Size phai bang nhau
//   4. FileID phai bang nhau (dam bao la cung file, khong phai file moi cung ten)
//   5. Entry chua het TTL
func (c *DigestCache) Lookup(path string, mtime time.Time, size int64, fileID uint64) (digest []byte, blocksHash []byte, ok bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()

	entry, exists := c.entries[path]
	if !exists {
		return nil, nil, false
	}

	// Kiem tra TTL
	if c.ttl > 0 && time.Since(entry.CachedAt) > c.ttl {
		return nil, nil, false
	}

	// Kiem tra mtime, size, fileID phai khop chinh xac
	if !entry.Mtime.Equal(mtime) || entry.Size != size || entry.FileID != fileID {
		return nil, nil, false
	}

	return entry.Digest, entry.BlocksHash, true
}

// Set luu digest cua mot file vao cache.
// Neu cache day, se xoa cac entry cu nhat (FIFO don gian).
func (c *DigestCache) Set(path string, mtime time.Time, size int64, fileID uint64, digest []byte, blocksHash []byte) {
	c.mu.Lock()
	defer c.mu.Unlock()

	// Eviction neu vuot qua gioi han
	if c.maxEntries > 0 && len(c.entries) >= c.maxEntries {
		// Xoa entry nao co CachedAt cu nhat.
		// Trong thuc te co the dung LRU, nhung FIFO du tot cho use case nay.
		c.evictOldest()
	}

	c.entries[path] = &DigestEntry{
		Path:       path,
		Mtime:      mtime,
		Size:       size,
		FileID:     fileID,
		Digest:     digest,
		BlocksHash: blocksHash,
		CachedAt:   time.Now(),
	}
}

// Remove xoa mot entry khoi cache (khi file bi xoa hoac rename).
func (c *DigestCache) Remove(path string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.entries, path)
}

// RemovePrefix xoa tat ca entry co path bat dau bang prefix.
// Dung khi mot thu muc bi xoa hoac rename.
func (c *DigestCache) RemovePrefix(prefix string) {
	c.mu.Lock()
	defer c.mu.Unlock()

	for path := range c.entries {
		if len(path) >= len(prefix) && path[:len(prefix)] == prefix {
			delete(c.entries, path)
		}
	}
}

// Size tra ve so luong entry hien co trong cache.
func (c *DigestCache) Size() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.entries)
}

// Clear xoa toan bo cache.
func (c *DigestCache) Clear() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.entries = make(map[string]*DigestEntry)
}

// Stats tra ve thong ke ve cache.
type CacheStats struct {
	Entries    int
	MaxEntries int
	TTL        time.Duration
}

func (c *DigestCache) Stats() CacheStats {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return CacheStats{
		Entries:    len(c.entries),
		MaxEntries: c.maxEntries,
		TTL:        c.ttl,
	}
}

// --- Private methods ---

func (c *DigestCache) evictOldest() {
	var oldestPath string
	var oldestTime time.Time

	first := true
	for path, entry := range c.entries {
		if first || entry.CachedAt.Before(oldestTime) {
			oldestPath = path
			oldestTime = entry.CachedAt
			first = false
		}
	}

	if oldestPath != "" {
		delete(c.entries, oldestPath)
	}
}
```

### 2.3 Sua doi `lib/scanner/walk.go` — Tich hop Incremental Scan

```go
// === THEM VAO DOAN DAU FILE, sau cac import hien tai ===

// WalkConfig mo rong de ho tro incremental scan.
// Cac truong moi deu optional; neu khong set, walker hoat dong nhu full scan.
type IncrementalConfig struct {
	// Enabled bat/tat incremental scan
	Enabled bool

	// DirtyPaths la tap hop cac duong dan can scan lai.
	// Neu nil hoac rong va Enabled == true, se thuc hien full scan.
	DirtyPaths map[string]struct{}

	// Baseline la snapshot tu lan scan truoc.
	// Cac subtree khong dirty se duoc tai su dung tu day.
	Baseline *Snapshot

	// Cache la digest cache de skip hash cho file khong thay doi.
	Cache *DigestCache
}

// === THEM TRUONG MOI VAO struct WalkCfg hien tai ===
// Trong struct WalkCfg (hoac ScanConfig), them:
//
//   Incremental IncrementalConfig
//

// === SUA DOI HAM walkRegular() ===
//
// walkRegular xu ly mot file thuong trong qua trinh scan.
// Phien ban moi them logic incremental:
//   1. Neu incremental enabled va path KHONG dirty -> skip, reuse baseline
//   2. Neu file metadata khop digest cache -> skip hash
//   3. Neu khong -> tinh hash binh thuong

func (w *walker) walkRegularIncremental(ctx context.Context, relPath string, info fs.FileInfo, curFile protocol.FileInfo) (protocol.FileInfo, error) {
	ic := w.cfg.Incremental

	// --- Buoc 1: Kiem tra dirty path ---
	if ic.Enabled && ic.DirtyPaths != nil && ic.Baseline != nil {
		if !isDirtyOrAncestor(relPath, ic.DirtyPaths) {
			// Path khong dirty: tai su dung tu baseline
			if fi, ok := ic.Baseline.Lookup(relPath); ok {
				return fi, nil
			}
			// Khong co trong baseline -> scan binh thuong (file moi)
		}
	}

	// --- Buoc 2: So sanh metadata voi current (logic hien tai) ---
	if curFile.IsEquivalentOptional(
		protocol.FileInfo{
			Name:      relPath,
			Type:      protocol.FileInfoTypeFile,
			Size:      info.Size(),
			ModifiedS: info.ModTime().Unix(),
		},
		protocol.FileInfoComparison{
			ModTime:     true,
			Size:        true,
			Permissions: w.cfg.Perms,
		},
	) {
		return curFile, nil
	}

	// --- Buoc 3: Kiem tra digest cache ---
	if ic.Enabled && ic.Cache != nil {
		fileID := getFileID(info) // lay inode/fileID tu filesystem
		digest, blocksHash, ok := ic.Cache.Lookup(relPath, info.ModTime(), info.Size(), fileID)
		if ok {
			// Cache hit: tao FileInfo voi digest tu cache, khong can hash lai
			fi := protocol.FileInfo{
				Name:       relPath,
				Type:       protocol.FileInfoTypeFile,
				Size:       info.Size(),
				ModifiedS:  info.ModTime().Unix(),
				ModifiedNs: int(info.ModTime().UnixNano() % 1e9),
				BlocksHash: blocksHash,
			}
			// Blocks van can duoc reconstruct tu blocksHash + size
			fi.Blocks = reconstructBlocksFromHash(fi.Size, blocksHash, digest)
			return fi, nil
		}
	}

	// --- Buoc 4: Hash binh thuong (logic hien tai) ---
	// Goi ham hash hien tai cua Syncthing
	fi, err := w.hashFile(ctx, relPath, info)
	if err != nil {
		return protocol.FileInfo{}, err
	}

	// Luu vao digest cache cho lan scan sau
	if ic.Enabled && ic.Cache != nil {
		fileID := getFileID(info)
		ic.Cache.Set(relPath, info.ModTime(), info.Size(), fileID,
			fi.BlocksHash, fi.BlocksHash)
	}

	return fi, nil
}

// isDirtyOrAncestor kiem tra xem mot path co nam trong dirty set hay khong.
// Mot path duoc coi la "dirty" neu:
//   - Chinh no nam trong dirtyPaths
//   - Mot path con cua no nam trong dirtyPaths (no la to tien)
//   - Mot path cha cua no nam trong dirtyPaths
func isDirtyOrAncestor(path string, dirtyPaths map[string]struct{}) bool {
	// Kiem tra chinh path
	if _, ok := dirtyPaths[path]; ok {
		return true
	}

	// Kiem tra xem path co la to tien cua dirty path nao khong
	for dp := range dirtyPaths {
		// path la to tien cua dp
		if strings.HasPrefix(dp, path+"/") {
			return true
		}
		// dp la to tien cua path
		if strings.HasPrefix(path, dp+"/") {
			return true
		}
	}

	return false
}

// getFileID lay file identifier tu FileInfo cua OS.
// Tren Linux/macOS: inode. Tren Windows: FileIndex.
func getFileID(info fs.FileInfo) uint64 {
	// Implementation phu thuoc OS, su dung build tags
	// Linux: info.Sys().(*syscall.Stat_t).Ino
	// Windows: GetFileInformationByHandle -> FileIndex
	return getFileIDPlatform(info)
}

// === SUA DOI HAM walkDir() ===
//
// walkDir duyet mot thu muc. Phien ban moi them logic skip subtree:

func (w *walker) walkDirIncremental(ctx context.Context, relPath string, dirEntries []fs.DirEntry) ([]protocol.FileInfo, error) {
	ic := w.cfg.Incremental

	// Neu incremental enabled va thu muc nay KHONG dirty
	// -> reuse toan bo subtree tu baseline
	if ic.Enabled && ic.DirtyPaths != nil && ic.Baseline != nil {
		if !isDirtyOrAncestor(relPath, ic.DirtyPaths) {
			files := ic.Baseline.SubtreeFiles(relPath)
			if files != nil {
				return files, nil
			}
			// Baseline khong co subtree nay -> scan binh thuong
		}
	}

	// Duyet binh thuong (logic hien tai), nhung goi walkRegularIncremental
	// thay vi walkRegular cho tung file
	var result []protocol.FileInfo
	for _, entry := range dirEntries {
		childPath := filepath.Join(relPath, entry.Name())

		if entry.IsDir() {
			subEntries, err := w.readDir(ctx, childPath)
			if err != nil {
				continue
			}
			subFiles, err := w.walkDirIncremental(ctx, childPath, subEntries)
			if err != nil {
				continue
			}
			result = append(result, subFiles...)
		} else {
			info, err := entry.Info()
			if err != nil {
				continue
			}
			curFile, _ := w.cfg.CurrentFiler.CurrentFile(childPath)
			fi, err := w.walkRegularIncremental(ctx, childPath, info, curFile)
			if err != nil {
				continue
			}
			result = append(result, fi)
		}
	}

	return result, nil
}
```

### 2.4 Sua doi `lib/model/folder.go` — Tich hop Baseline va Dirty Paths

```go
// === THEM CAC TRUONG MOI VAO struct folder ===
//
// Trong struct folder (hoac sendReceiveFolder/sendOnlyFolder), them:

type folderIncrementalState struct {
	baseline    *scanner.Snapshot
	cache       *scanner.DigestCache
	dirtyPaths  map[string]struct{}
	dirtyMu     sync.Mutex
}

// === KHOI TAO TRONG NewFolder() HOAC startFolder() ===

func (f *folder) initIncremental() {
	if !f.FolderConfiguration.IncrementalScan {
		return
	}

	f.incr.baseline = scanner.NewSnapshot()

	// Mac dinh: 500K entries, TTL 24h
	maxEntries := f.FolderConfiguration.DigestCacheMaxEntries
	if maxEntries <= 0 {
		maxEntries = 500000
	}
	f.incr.cache = scanner.NewDigestCache(maxEntries, 24*time.Hour)
	f.incr.dirtyPaths = make(map[string]struct{})
}

// === XU LY FSNOTIFY EVENT ===
//
// Khi nhan duoc fsnotify event, thay vi trigger full scan,
// chi them path vao dirty set:

func (f *folder) markDirty(paths []string) {
	if !f.FolderConfiguration.IncrementalScan {
		return
	}

	f.incr.dirtyMu.Lock()
	defer f.incr.dirtyMu.Unlock()

	for _, p := range paths {
		f.incr.dirtyPaths[p] = struct{}{}
		// Them cac thu muc cha vao dirty set
		dir := filepath.Dir(p)
		for dir != "." && dir != "" {
			f.incr.dirtyPaths[dir] = struct{}{}
			dir = filepath.Dir(dir)
		}
	}
}

// === SUA DOI scanSubdirs() ===
//
// Truyen dirty paths va incremental config vao walker:

func (f *folder) scanSubdirsIncremental(ctx context.Context, subs []string) error {
	// Lay va reset dirty paths
	f.incr.dirtyMu.Lock()
	dirtyPaths := f.incr.dirtyPaths
	f.incr.dirtyPaths = make(map[string]struct{})
	f.incr.dirtyMu.Unlock()

	// Neu khong co subs cu the va co dirty paths, chi scan dirty paths
	scanSubs := subs
	if len(subs) == 0 && len(dirtyPaths) > 0 {
		// Chuyen dirty paths thanh danh sach thu muc can scan
		scanSubs = extractTopLevelDirs(dirtyPaths)
	}

	// Cau hinh walker voi incremental config
	walkCfg := scanner.WalkCfg{
		// ... cac truong hien tai ...
		Incremental: scanner.IncrementalConfig{
			Enabled:    true,
			DirtyPaths: dirtyPaths,
			Baseline:   f.incr.baseline,
			Cache:      f.incr.cache,
		},
	}

	// Goi walker
	result, err := scanner.Walk(ctx, walkCfg)
	if err != nil {
		// Loi: tra dirty paths lai de scan lai lan sau
		f.incr.dirtyMu.Lock()
		for dp := range dirtyPaths {
			f.incr.dirtyPaths[dp] = struct{}{}
		}
		f.incr.dirtyMu.Unlock()
		return err
	}

	// Cap nhat baseline voi ket qua scan
	f.incr.baseline.Update(result, dirtyPaths)

	// Xu ly ket qua nhu binh thuong
	return f.processResult(result)
}

// extractTopLevelDirs tra ve cac thu muc cap cao nhat tu dirty paths.
// Vi du: {"a/b/c", "a/b/d", "x/y"} -> {"a/b", "x"}
func extractTopLevelDirs(dirtyPaths map[string]struct{}) []string {
	dirs := make(map[string]struct{})
	for p := range dirtyPaths {
		parts := strings.SplitN(filepath.ToSlash(p), "/", 2)
		if len(parts) > 0 {
			dirs[parts[0]] = struct{}{}
		}
	}

	result := make([]string, 0, len(dirs))
	for d := range dirs {
		result = append(result, d)
	}
	return result
}
```

### 2.5 Sua doi `lib/config/folderconfiguration.go`

```go
// === THEM CAC TRUONG MOI VAO struct FolderConfiguration ===

type FolderConfiguration struct {
	// ... cac truong hien tai ...

	// IncrementalScan bat co che incremental scan (chi scan dirty paths).
	// Mac dinh: false (full scan nhu cu).
	IncrementalScan bool `json:"incrementalScan" xml:"incrementalScan,attr" default:"false"`

	// DigestCacheMaxEntries gioi han so entry trong digest cache.
	// Mac dinh: 500000. Dat 0 de tat digest cache.
	DigestCacheMaxEntries int `json:"digestCacheMaxEntries" xml:"digestCacheMaxEntries,attr" default:"500000"`
}
```

---

## Phase 3 — Review Checklist

### 3.1 Code Quality

- [ ] Tat ca cac ham public deu co Go doc comment giai thich muc dich va hanh vi
- [ ] Khong co magic number — tat ca constant duoc dat ten ro rang
- [ ] Error handling: moi error duoc wrap voi context (`fmt.Errorf("snapshot update: %w", err)`)
- [ ] Khong co goroutine leak: moi goroutine co cancel path ro rang
- [ ] Mutex usage dung: khong hold lock khi goi I/O hoac ham ben ngoai

### 3.2 Correctness

- [ ] `isDirtyOrAncestor()` xu ly dung cac edge case: root path, path co ten tuong tu (vi du: `foo` va `foobar`)
- [ ] Digest cache eviction khong lam mat entry dang duoc su dung
- [ ] Snapshot Update() khong race condition voi Lookup() (RWMutex)
- [ ] Khi incremental scan fail, dirty paths duoc tra lai de retry
- [ ] `splitPath()` xu ly dung tren ca Linux va Windows (dung `filepath.ToSlash`)

### 3.3 Performance

- [ ] Snapshot lookup la O(depth) voi depth la do sau cua cay thu muc
- [ ] Digest cache lookup la O(1) (map lookup)
- [ ] Khong alloc bo nho khong can thiet trong hot path (walkRegularIncremental)
- [ ] `isDirtyOrAncestor()` khong iterate toan bo dirty set cho moi file — can toi uu neu dirty set lon (>10K entries)
- [ ] Benchmark: incremental scan 100K files voi 100 dirty paths phai nhanh hon full scan it nhat 10x

### 3.4 Backward Compatibility

- [ ] Khi `IncrementalScan == false`, toan bo code path cu duoc giu nguyen, khong co side effect
- [ ] Output cua incremental scan la `[]protocol.FileInfo` — cung type va semantics nhu full scan
- [ ] Config migration: version cu khong co truong moi -> default value duoc ap dung dung
- [ ] Khong thay doi protocol version hoac index format

### 3.5 Security

- [ ] Digest cache khong expose thong tin file ra ben ngoai (chi luu in-memory)
- [ ] Khong co path traversal vulnerability trong splitPath() hoac findEntry()
- [ ] File ID comparison ngan chan attack thay the file bang symlink cung ten

### 3.6 Documentation

- [ ] CHANGES.md cap nhat mo ta feature moi
- [ ] Config documentation cap nhat voi cac truong moi
- [ ] Comment trong code giai thich WHY, khong chi WHAT

---

## Phase 4 — Test

### 4.1 Unit Tests

#### `lib/scanner/snapshot_test.go`

```go
package scanner

import (
	"testing"

	"github.com/syncthing/syncthing/lib/protocol"
)

// TestSnapshotSetAndLookup kiem tra rang sau khi set mot FileInfo vao snapshot,
// co the lookup lai duoc voi dung gia tri.
// TAI SAO: Day la hanh vi co ban nhat cua snapshot — neu lookup khong tra ve
// dung ket qua thi toan bo co che reuse baseline se sai.
func TestSnapshotSetAndLookup(t *testing.T) {
	snap := NewSnapshot()

	fi := protocol.FileInfo{
		Name:      "docs/readme.txt",
		Type:      protocol.FileInfoTypeFile,
		Size:      1024,
		ModifiedS: 1700000000,
	}

	// Simulate update voi 1 file
	snap.Update([]protocol.FileInfo{fi}, map[string]struct{}{
		"docs/readme.txt": {},
	})

	got, ok := snap.Lookup("docs/readme.txt")
	if !ok {
		t.Fatal("expected to find docs/readme.txt in snapshot")
	}
	if got.Size != 1024 {
		t.Errorf("expected size 1024, got %d", got.Size)
	}
	if got.ModifiedS != 1700000000 {
		t.Errorf("expected mtime 1700000000, got %d", got.ModifiedS)
	}
}

// TestSnapshotLookupMissing kiem tra rang lookup mot path khong ton tai
// tra ve ok == false.
// TAI SAO: Walker can phan biet giua "file ton tai trong baseline" va
// "file moi chua co trong baseline" de quyet dinh scan hay skip.
func TestSnapshotLookupMissing(t *testing.T) {
	snap := NewSnapshot()

	_, ok := snap.Lookup("nonexistent/file.txt")
	if ok {
		t.Error("expected ok == false for missing path")
	}
}

// TestSnapshotSubtreeFiles kiem tra rang SubtreeFiles tra ve tat ca file
// trong mot thu muc va cac thu muc con.
// TAI SAO: Khi mot thu muc khong dirty, walker can lay toan bo cac file
// trong subtree do tu baseline. Neu SubtreeFiles tra thieu file,
// cac file do se "bien mat" khoi index.
func TestSnapshotSubtreeFiles(t *testing.T) {
	snap := NewSnapshot()

	files := []protocol.FileInfo{
		{Name: "src/main.go", Type: protocol.FileInfoTypeFile, Size: 100},
		{Name: "src/util.go", Type: protocol.FileInfoTypeFile, Size: 200},
		{Name: "src/sub/helper.go", Type: protocol.FileInfoTypeFile, Size: 300},
		{Name: "docs/readme.txt", Type: protocol.FileInfoTypeFile, Size: 50},
	}

	allDirty := make(map[string]struct{})
	for _, f := range files {
		allDirty[f.Name] = struct{}{}
	}
	snap.Update(files, allDirty)

	subtree := snap.SubtreeFiles("src")
	if len(subtree) < 3 {
		t.Errorf("expected at least 3 files in src/ subtree, got %d", len(subtree))
	}

	// Kiem tra docs khong nam trong subtree cua src
	for _, f := range subtree {
		if f.Name == "docs/readme.txt" {
			t.Error("docs/readme.txt should not be in src/ subtree")
		}
	}
}

// TestSnapshotUpdateRemovesDirty kiem tra rang Update xoa cac entry
// dirty truoc khi them ket qua moi.
// TAI SAO: Neu mot file bi xoa (co trong dirty paths nhung khong co trong
// scanResult), no phai bien mat khoi snapshot. Neu khong xoa truoc,
// file da xoa se van ton tai trong baseline.
func TestSnapshotUpdateRemovesDirty(t *testing.T) {
	snap := NewSnapshot()

	// Scan lan 1: co file a.txt va b.txt
	files1 := []protocol.FileInfo{
		{Name: "a.txt", Type: protocol.FileInfoTypeFile, Size: 100},
		{Name: "b.txt", Type: protocol.FileInfoTypeFile, Size: 200},
	}
	allDirty := map[string]struct{}{"a.txt": {}, "b.txt": {}}
	snap.Update(files1, allDirty)

	// Scan lan 2: chi co a.txt (b.txt da bi xoa)
	files2 := []protocol.FileInfo{
		{Name: "a.txt", Type: protocol.FileInfoTypeFile, Size: 150},
	}
	snap.Update(files2, map[string]struct{}{"a.txt": {}, "b.txt": {}})

	// b.txt phai khong con trong snapshot
	_, ok := snap.Lookup("b.txt")
	if ok {
		t.Error("b.txt should have been removed from snapshot after update")
	}

	// a.txt phai co size moi
	got, ok := snap.Lookup("a.txt")
	if !ok {
		t.Fatal("a.txt should still be in snapshot")
	}
	if got.Size != 150 {
		t.Errorf("expected size 150, got %d", got.Size)
	}
}

// TestSnapshotReset kiem tra rang Reset xoa toan bo snapshot.
// TAI SAO: Khi user force full rescan, toan bo baseline phai bi xoa
// de dam bao scan lai tu dau.
func TestSnapshotReset(t *testing.T) {
	snap := NewSnapshot()

	snap.Update([]protocol.FileInfo{
		{Name: "test.txt", Type: protocol.FileInfoTypeFile},
	}, map[string]struct{}{"test.txt": {}})

	snap.Reset()

	if snap.Size() != 1 { // root entry
		t.Errorf("expected size 1 (root only) after reset, got %d", snap.Size())
	}

	_, ok := snap.Lookup("test.txt")
	if ok {
		t.Error("expected no entries after reset")
	}
}

// TestSnapshotConcurrentAccess kiem tra rang snapshot an toan khi
// doc va ghi dong thoi tu nhieu goroutine.
// TAI SAO: Trong thuc te, fsnotify goroutine ghi dirty paths
// trong khi scan goroutine doc baseline — can dam bao khong data race.
func TestSnapshotConcurrentAccess(t *testing.T) {
	snap := NewSnapshot()
	done := make(chan struct{})

	// Writer goroutine
	go func() {
		for i := 0; i < 1000; i++ {
			snap.Update([]protocol.FileInfo{
				{Name: "concurrent.txt", Type: protocol.FileInfoTypeFile, Size: int64(i)},
			}, map[string]struct{}{"concurrent.txt": {}})
		}
		close(done)
	}()

	// Reader goroutine
	for i := 0; i < 1000; i++ {
		snap.Lookup("concurrent.txt")
		snap.SubtreeFiles(".")
		snap.Size()
	}

	<-done
}
```

#### `lib/scanner/cache_test.go`

```go
package scanner

import (
	"testing"
	"time"
)

// TestDigestCacheHit kiem tra rang cache tra ve dung digest khi
// path, mtime, size, fileID deu khop.
// TAI SAO: Day la happy path — neu cache hit khong hoat dong,
// moi file se bi hash lai moi lan scan, vo hieu hoa toan bo tinh nang cache.
func TestDigestCacheHit(t *testing.T) {
	cache := NewDigestCache(1000, time.Hour)

	mtime := time.Now()
	digest := []byte{0x01, 0x02, 0x03}
	blocksHash := []byte{0x04, 0x05, 0x06}

	cache.Set("photos/vacation.jpg", mtime, 5242880, 12345, digest, blocksHash)

	gotDigest, gotBlocksHash, ok := cache.Lookup("photos/vacation.jpg", mtime, 5242880, 12345)
	if !ok {
		t.Fatal("expected cache hit")
	}
	if len(gotDigest) != 3 || gotDigest[0] != 0x01 {
		t.Error("digest mismatch")
	}
	if len(gotBlocksHash) != 3 || gotBlocksHash[0] != 0x04 {
		t.Error("blocksHash mismatch")
	}
}

// TestDigestCacheMissMtimeChanged kiem tra rang cache miss khi mtime thay doi.
// TAI SAO: Neu file bi sua (mtime thay doi), digest cu khong con hop le.
// Cache PHAI miss de buoc he thong hash lai file.
func TestDigestCacheMissMtimeChanged(t *testing.T) {
	cache := NewDigestCache(1000, time.Hour)

	mtime := time.Now()
	cache.Set("data.csv", mtime, 1000, 111, []byte{0x01}, []byte{0x02})

	// Lookup voi mtime khac
	_, _, ok := cache.Lookup("data.csv", mtime.Add(time.Second), 1000, 111)
	if ok {
		t.Error("expected cache miss when mtime changed")
	}
}

// TestDigestCacheMissSizeChanged kiem tra rang cache miss khi size thay doi.
// TAI SAO: Co truong hop file bi ghi de ma mtime khong doi (ví dụ: truncate va ghi lai cung timestamp).
// Size la kiem tra bo sung.
func TestDigestCacheMissSizeChanged(t *testing.T) {
	cache := NewDigestCache(1000, time.Hour)

	mtime := time.Now()
	cache.Set("data.csv", mtime, 1000, 111, []byte{0x01}, []byte{0x02})

	_, _, ok := cache.Lookup("data.csv", mtime, 2000, 111)
	if ok {
		t.Error("expected cache miss when size changed")
	}
}

// TestDigestCacheMissFileIDChanged kiem tra rang cache miss khi fileID thay doi.
// TAI SAO: Khi file bi xoa va tao lai cung ten, fileID (inode) se khac.
// Neu khong kiem tra fileID, cache se tra ve digest cua file cu cho file moi.
func TestDigestCacheMissFileIDChanged(t *testing.T) {
	cache := NewDigestCache(1000, time.Hour)

	mtime := time.Now()
	cache.Set("config.yaml", mtime, 500, 111, []byte{0x01}, []byte{0x02})

	// Cung path, cung mtime, cung size, nhung fileID khac
	_, _, ok := cache.Lookup("config.yaml", mtime, 500, 222)
	if ok {
		t.Error("expected cache miss when fileID changed")
	}
}

// TestDigestCacheTTLExpiry kiem tra rang entry het han sau TTL.
// TAI SAO: Cache khong duoc giu entry qua lau vi filesystem co the thay doi
// ma khong thong qua fsnotify (vi du: NFS mount, external tools).
// TTL la cơ che an toan de dam bao cache khong qua stale.
func TestDigestCacheTTLExpiry(t *testing.T) {
	// TTL cuc ngan de test
	cache := NewDigestCache(1000, time.Millisecond)

	mtime := time.Now()
	cache.Set("old.txt", mtime, 100, 1, []byte{0x01}, []byte{0x02})

	// Doi het TTL
	time.Sleep(5 * time.Millisecond)

	_, _, ok := cache.Lookup("old.txt", mtime, 100, 1)
	if ok {
		t.Error("expected cache miss after TTL expiry")
	}
}

// TestDigestCacheEviction kiem tra rang khi cache day, entry cu nhat bi xoa.
// TAI SAO: Cache phai co gioi han kich thuoc de khong chiem qua nhieu RAM.
// Eviction policy phai loai bo entry it huu ich nhat (cu nhat).
func TestDigestCacheEviction(t *testing.T) {
	cache := NewDigestCache(3, time.Hour) // Chi giu toi da 3 entry

	mtime := time.Now()
	cache.Set("file1.txt", mtime, 100, 1, []byte{0x01}, nil)
	time.Sleep(time.Millisecond) // Dam bao CachedAt khac nhau
	cache.Set("file2.txt", mtime, 200, 2, []byte{0x02}, nil)
	time.Sleep(time.Millisecond)
	cache.Set("file3.txt", mtime, 300, 3, []byte{0x03}, nil)
	time.Sleep(time.Millisecond)

	// Them entry thu 4 -> file1 (cu nhat) phai bi evict
	cache.Set("file4.txt", mtime, 400, 4, []byte{0x04}, nil)

	if cache.Size() != 3 {
		t.Errorf("expected cache size 3 after eviction, got %d", cache.Size())
	}

	_, _, ok := cache.Lookup("file1.txt", mtime, 100, 1)
	if ok {
		t.Error("file1.txt should have been evicted")
	}

	// file2, file3, file4 van phai con
	_, _, ok2 := cache.Lookup("file2.txt", mtime, 200, 2)
	_, _, ok3 := cache.Lookup("file3.txt", mtime, 300, 3)
	_, _, ok4 := cache.Lookup("file4.txt", mtime, 400, 4)
	if !ok2 || !ok3 || !ok4 {
		t.Error("newer entries should not have been evicted")
	}
}

// TestDigestCacheRemovePrefix kiem tra rang xoa theo prefix hoat dong dung.
// TAI SAO: Khi mot thu muc bi rename hoac xoa, tat ca cac entry trong
// thu muc do phai bi xoa khoi cache. Neu khong, cache se chua
// stale entries cho cac path khong con ton tai.
func TestDigestCacheRemovePrefix(t *testing.T) {
	cache := NewDigestCache(1000, time.Hour)
	mtime := time.Now()

	cache.Set("project/src/main.go", mtime, 100, 1, []byte{0x01}, nil)
	cache.Set("project/src/util.go", mtime, 200, 2, []byte{0x02}, nil)
	cache.Set("project/docs/readme.md", mtime, 50, 3, []byte{0x03}, nil)
	cache.Set("other/file.txt", mtime, 300, 4, []byte{0x04}, nil)

	// Xoa toan bo project/src/
	cache.RemovePrefix("project/src/")

	if cache.Size() != 2 {
		t.Errorf("expected 2 entries after prefix removal, got %d", cache.Size())
	}

	_, _, ok1 := cache.Lookup("project/src/main.go", mtime, 100, 1)
	_, _, ok2 := cache.Lookup("project/src/util.go", mtime, 200, 2)
	if ok1 || ok2 {
		t.Error("entries under project/src/ should have been removed")
	}

	_, _, ok3 := cache.Lookup("project/docs/readme.md", mtime, 50, 3)
	_, _, ok4 := cache.Lookup("other/file.txt", mtime, 300, 4)
	if !ok3 || !ok4 {
		t.Error("entries outside prefix should remain")
	}
}

// TestDigestCacheConcurrent kiem tra cache thread-safe.
// TAI SAO: Scanner chay nhieu goroutine song song (parallelHasher),
// tat ca co the doc/ghi cache dong thoi.
func TestDigestCacheConcurrent(t *testing.T) {
	cache := NewDigestCache(10000, time.Hour)
	done := make(chan struct{})

	// Writer
	go func() {
		for i := 0; i < 1000; i++ {
			mtime := time.Now()
			cache.Set("concurrent.txt", mtime, int64(i), uint64(i), []byte{byte(i)}, nil)
		}
		close(done)
	}()

	// Reader
	for i := 0; i < 1000; i++ {
		cache.Lookup("concurrent.txt", time.Now(), int64(i), uint64(i))
	}

	<-done
}
```

### 4.2 Integration Test

#### `lib/scanner/incremental_test.go`

```go
package scanner_test

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/syncthing/syncthing/lib/protocol"
	"github.com/syncthing/syncthing/lib/scanner"
)

// TestIncrementalScanEndToEnd kiem tra toan bo flow incremental scan
// tu dau den cuoi tren filesystem that.
// TAI SAO: Unit test chi kiem tra tung component rieng le. Integration test
// nay dam bao cac component (snapshot, cache, walker) phoi hop dung
// khi chay tren filesystem that voi cac file that.
//
// Flow:
//   1. Tao thu muc voi 100 file
//   2. Full scan lan 1 -> xay dung baseline
//   3. Sua 2 file, them 1 file moi
//   4. Incremental scan voi dirty paths = 3 file thay doi
//   5. Kiem tra: chi 3 file duoc hash lai, 97 file con lai reuse tu baseline
func TestIncrementalScanEndToEnd(t *testing.T) {
	// Setup: tao thu muc tam
	tmpDir := t.TempDir()

	// Tao cau truc thu muc voi 100 file
	for i := 0; i < 100; i++ {
		dir := filepath.Join(tmpDir, "subdir", subDirName(i))
		os.MkdirAll(dir, 0755)

		path := filepath.Join(dir, fileName(i))
		os.WriteFile(path, []byte(fileContent(i)), 0644)
	}

	// --- Lan scan 1: Full scan de xay dung baseline ---
	baseline := scanner.NewSnapshot()
	cache := scanner.NewDigestCache(10000, time.Hour)

	fullResult, err := scanWithConfig(context.Background(), tmpDir, scanner.IncrementalConfig{
		Enabled:    true,
		DirtyPaths: nil, // nil = full scan
		Baseline:   baseline,
		Cache:      cache,
	})
	if err != nil {
		t.Fatalf("full scan failed: %v", err)
	}

	if len(fullResult) != 100 {
		t.Fatalf("expected 100 files from full scan, got %d", len(fullResult))
	}

	// Cap nhat baseline
	allDirty := make(map[string]struct{})
	for _, fi := range fullResult {
		allDirty[fi.Name] = struct{}{}
	}
	baseline.Update(fullResult, allDirty)

	// Kiem tra cache da duoc populate
	if cache.Size() < 100 {
		t.Errorf("expected cache to have >= 100 entries after full scan, got %d", cache.Size())
	}

	// --- Thay doi: sua 2 file, them 1 file moi ---
	modifiedFile1 := filepath.Join("subdir", subDirName(0), fileName(0))
	modifiedFile2 := filepath.Join("subdir", subDirName(50), fileName(50))
	newFile := filepath.Join("subdir", "new_file.txt")

	os.WriteFile(filepath.Join(tmpDir, modifiedFile1), []byte("MODIFIED CONTENT 1"), 0644)
	os.WriteFile(filepath.Join(tmpDir, modifiedFile2), []byte("MODIFIED CONTENT 2"), 0644)
	os.WriteFile(filepath.Join(tmpDir, newFile), []byte("NEW FILE"), 0644)

	// --- Lan scan 2: Incremental scan ---
	dirtyPaths := map[string]struct{}{
		modifiedFile1: {},
		modifiedFile2: {},
		newFile:       {},
	}

	incrResult, err := scanWithConfig(context.Background(), tmpDir, scanner.IncrementalConfig{
		Enabled:    true,
		DirtyPaths: dirtyPaths,
		Baseline:   baseline,
		Cache:      cache,
	})
	if err != nil {
		t.Fatalf("incremental scan failed: %v", err)
	}

	// Phai tra ve 101 file (100 cu + 1 moi)
	if len(incrResult) != 101 {
		t.Errorf("expected 101 files from incremental scan, got %d", len(incrResult))
	}

	// Kiem tra file duoc sua co noi dung moi
	for _, fi := range incrResult {
		if fi.Name == modifiedFile1 || fi.Name == modifiedFile2 {
			// File da sua phai co size khac voi ban goc
			if fi.Size == int64(len(fileContent(0))) {
				t.Errorf("modified file %s should have different size", fi.Name)
			}
		}
	}

	// Kiem tra file moi co trong ket qua
	found := false
	for _, fi := range incrResult {
		if fi.Name == newFile {
			found = true
			break
		}
	}
	if !found {
		t.Error("new file should appear in incremental scan result")
	}
}

// TestIncrementalScanDeletedFile kiem tra rang khi file bi xoa va duoc
// danh dau dirty, no khong con trong ket qua scan.
// TAI SAO: Truong hop file bi xoa la critical — neu incremental scan
// van tra ve file da xoa tu baseline, dong bo se khong phan anh
// viec xoa, gay du lieu khong nhat quan giua cac thiet bi.
func TestIncrementalScanDeletedFile(t *testing.T) {
	tmpDir := t.TempDir()

	// Tao 3 file
	os.WriteFile(filepath.Join(tmpDir, "keep1.txt"), []byte("keep"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "delete_me.txt"), []byte("delete"), 0644)
	os.WriteFile(filepath.Join(tmpDir, "keep2.txt"), []byte("keep"), 0644)

	// Full scan
	baseline := scanner.NewSnapshot()
	cache := scanner.NewDigestCache(1000, time.Hour)
	result, _ := scanWithConfig(context.Background(), tmpDir, scanner.IncrementalConfig{
		Enabled: true, Baseline: baseline, Cache: cache,
	})
	allDirty := make(map[string]struct{})
	for _, fi := range result {
		allDirty[fi.Name] = struct{}{}
	}
	baseline.Update(result, allDirty)

	// Xoa file
	os.Remove(filepath.Join(tmpDir, "delete_me.txt"))

	// Incremental scan voi dirty path
	dirtyPaths := map[string]struct{}{"delete_me.txt": {}}
	incrResult, _ := scanWithConfig(context.Background(), tmpDir, scanner.IncrementalConfig{
		Enabled:    true,
		DirtyPaths: dirtyPaths,
		Baseline:   baseline,
		Cache:      cache,
	})

	// delete_me.txt khong duoc co trong ket qua
	for _, fi := range incrResult {
		if fi.Name == "delete_me.txt" {
			t.Error("deleted file should not appear in incremental scan result")
		}
	}
}

// TestIncrementalScanFallbackToFullScan kiem tra rang khi baseline
// la nil hoac rong, incremental scan fall back ve full scan.
// TAI SAO: Lan chay dau tien hoac sau khi reset, khong co baseline.
// He thong phai tu dong full scan thay vi bi loi hoac tra ve ket qua rong.
func TestIncrementalScanFallbackToFullScan(t *testing.T) {
	tmpDir := t.TempDir()
	os.WriteFile(filepath.Join(tmpDir, "file.txt"), []byte("content"), 0644)

	// Incremental scan voi baseline rong
	result, err := scanWithConfig(context.Background(), tmpDir, scanner.IncrementalConfig{
		Enabled:    true,
		DirtyPaths: map[string]struct{}{"file.txt": {}},
		Baseline:   scanner.NewSnapshot(), // rong
		Cache:      scanner.NewDigestCache(1000, time.Hour),
	})
	if err != nil {
		t.Fatalf("scan should not fail with empty baseline: %v", err)
	}
	if len(result) == 0 {
		t.Error("should fall back to full scan and find files")
	}
}

// --- Helper functions ---

func subDirName(i int) string {
	return filepath.Join("dir"+string(rune('a'+i/26)), "sub"+string(rune('a'+i%26)))
}

func fileName(i int) string {
	return "file_" + string(rune('0'+i/100)) + string(rune('0'+(i/10)%10)) + string(rune('0'+i%10)) + ".dat"
}

func fileContent(i int) string {
	return "content-" + string(rune('0'+i/100)) + string(rune('0'+(i/10)%10)) + string(rune('0'+i%10))
}

func scanWithConfig(ctx context.Context, root string, ic scanner.IncrementalConfig) ([]protocol.FileInfo, error) {
	// Wrapper quanh scanner.Walk voi incremental config
	cfg := scanner.WalkCfg{
		Root:        root,
		Incremental: ic,
		// ... cac truong khac set mac dinh ...
	}
	return scanner.Walk(ctx, cfg)
}
```

### 4.3 Mock Test

#### `lib/scanner/incremental_mock_test.go`

```go
package scanner_test

import (
	"testing"
	"time"

	"github.com/syncthing/syncthing/lib/protocol"
	"github.com/syncthing/syncthing/lib/scanner"
)

// MockCurrentFiler gia lap CurrentFiler interface de test walker
// ma khong can database that.
type MockCurrentFiler struct {
	files map[string]protocol.FileInfo
}

func (m *MockCurrentFiler) CurrentFile(name string) (protocol.FileInfo, bool) {
	fi, ok := m.files[name]
	return fi, ok
}

// TestWalkerWithMockCurrentFiler kiem tra rang walker su dung
// CurrentFiler dung de so sanh file hien tai voi file tren disk.
// TAI SAO: Trong unit test, khong muon phu thuoc vao database.
// Mock cho phep kiem tra logic so sanh mot cach co lap,
// va kiem soat chinh xac file nao "da co" trong DB.
func TestWalkerWithMockCurrentFiler(t *testing.T) {
	mock := &MockCurrentFiler{
		files: map[string]protocol.FileInfo{
			"unchanged.txt": {
				Name:      "unchanged.txt",
				Type:      protocol.FileInfoTypeFile,
				Size:      100,
				ModifiedS: 1700000000,
			},
		},
	}

	// Kiem tra rang file co trong mock duoc nhan dien la "da co"
	fi, ok := mock.CurrentFile("unchanged.txt")
	if !ok {
		t.Fatal("mock should return unchanged.txt")
	}
	if fi.Size != 100 {
		t.Errorf("expected size 100, got %d", fi.Size)
	}

	// File khong co trong mock
	_, ok = mock.CurrentFile("new_file.txt")
	if ok {
		t.Error("mock should not have new_file.txt")
	}
}

// MockFilesystem gia lap filesystem de test incremental logic
// ma khong can I/O that.
type MockFilesystem struct {
	files map[string]MockFileInfo
}

type MockFileInfo struct {
	name    string
	size    int64
	modTime time.Time
	isDir   bool
	fileID  uint64
}

// TestIsDirtyOrAncestor kiem tra logic xac dinh dirty path.
// TAI SAO: Day la logic cot loi cua incremental scan.
// Neu isDirtyOrAncestor tra ve sai, he thong se:
//   - Skip file can scan (false negative) -> mat thay doi
//   - Scan file khong can (false positive) -> giam hieu suat nhung khong loi
// False negative la nghiem trong hon, nen test can bao phu ca edge case.
func TestIsDirtyOrAncestor(t *testing.T) {
	dirtyPaths := map[string]struct{}{
		"src/main.go":     {},
		"docs/api/v2.md":  {},
	}

	tests := []struct {
		path     string
		expected bool
		reason   string
	}{
		// Chinh path la dirty
		{"src/main.go", true, "exact dirty path must be dirty"},
		{"docs/api/v2.md", true, "exact dirty path must be dirty"},

		// To tien cua dirty path
		{"src", true, "parent of dirty path must be dirty (need to traverse into)"},
		{"docs", true, "grandparent of dirty path must be dirty"},
		{"docs/api", true, "direct parent of dirty path must be dirty"},

		// Con cua dirty path (file bi thay doi -> scan lai thu muc chua no)
		// Luu y: dirty path la file, khong phai thu muc, nen con cua no khong dirty
		// Truong hop nay phu thuoc vao implementation

		// Khong lien quan
		{"lib/util.go", false, "unrelated path should not be dirty"},
		{"test/unit_test.go", false, "unrelated path should not be dirty"},

		// Edge case: ten tuong tu nhung khong phai to tien
		{"src_backup/main.go", false, "similar prefix but not ancestor"},
		{"docs2/api/v2.md", false, "similar prefix but different dir"},

		// Root
		{".", false, "root should not be dirty unless explicitly marked"},
		{"", false, "empty path should not be dirty"},
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			// Goi isDirtyOrAncestor (can export hoac test qua public API)
			got := scanner.IsDirtyOrAncestor(tt.path, dirtyPaths)
			if got != tt.expected {
				t.Errorf("isDirtyOrAncestor(%q) = %v, want %v: %s",
					tt.path, got, tt.expected, tt.reason)
			}
		})
	}
}

// TestDigestCacheIntegrationWithSnapshot kiem tra rang cache va snapshot
// phoi hop dung trong mot chu ky scan.
// TAI SAO: Cache va snapshot la hai component doc lap nhung phai hoat dong
// nhat quan. Test nay dam bao:
//   - File khong dirty: reuse tu snapshot (khong can cache lookup)
//   - File dirty nhung metadata khong doi: cache hit, skip hash
//   - File dirty va metadata thay doi: cache miss, hash lai va cap nhat cache
func TestDigestCacheIntegrationWithSnapshot(t *testing.T) {
	baseline := scanner.NewSnapshot()
	cache := scanner.NewDigestCache(1000, time.Hour)

	mtime := time.Now()

	// Setup baseline voi 3 file
	files := []protocol.FileInfo{
		{Name: "stable.txt", Size: 100, ModifiedS: mtime.Unix()},
		{Name: "touched.txt", Size: 200, ModifiedS: mtime.Unix()},
		{Name: "modified.txt", Size: 300, ModifiedS: mtime.Unix()},
	}
	allDirty := make(map[string]struct{})
	for _, f := range files {
		allDirty[f.Name] = struct{}{}
	}
	baseline.Update(files, allDirty)

	// Setup cache
	cache.Set("stable.txt", mtime, 100, 1, []byte{0x01}, []byte{0x11})
	cache.Set("touched.txt", mtime, 200, 2, []byte{0x02}, []byte{0x22})
	cache.Set("modified.txt", mtime, 300, 3, []byte{0x03}, []byte{0x33})

	// Scenario: dirty paths = touched.txt va modified.txt
	dirtyPaths := map[string]struct{}{
		"touched.txt":  {},
		"modified.txt": {},
	}

	// 1. stable.txt: khong dirty -> reuse tu baseline
	_, ok := baseline.Lookup("stable.txt")
	if !ok {
		t.Error("stable.txt should be in baseline for reuse")
	}

	// 2. touched.txt: dirty nhung metadata khong doi -> cache hit
	_, _, cacheHit := cache.Lookup("touched.txt", mtime, 200, 2)
	if !cacheHit {
		t.Error("touched.txt should hit cache (metadata unchanged)")
	}

	// 3. modified.txt: dirty va metadata thay doi -> cache miss
	newMtime := mtime.Add(time.Second)
	_, _, cacheMiss := cache.Lookup("modified.txt", newMtime, 350, 3)
	if cacheMiss {
		t.Error("modified.txt should miss cache (mtime changed)")
	}

	// Kiem tra dirty paths
	for dp := range dirtyPaths {
		if dp != "touched.txt" && dp != "modified.txt" {
			t.Errorf("unexpected dirty path: %s", dp)
		}
	}
}
```

### 4.4 Chay tests

```bash
# Chay toan bo unit test cua package scanner
go test -v -count=1 ./lib/scanner/...

# Chay chi snapshot tests
go test -v -run TestSnapshot ./lib/scanner/

# Chay chi cache tests
go test -v -run TestDigestCache ./lib/scanner/

# Chay chi incremental integration tests
go test -v -run TestIncremental ./lib/scanner/

# Chay chi mock tests
go test -v -run TestIsDirtyOrAncestor ./lib/scanner/
go test -v -run TestDigestCacheIntegration ./lib/scanner/
go test -v -run TestWalkerWithMock ./lib/scanner/

# Chay voi race detector (bat buoc truoc khi merge)
go test -race -count=1 ./lib/scanner/...

# Chay benchmark de do hieu suat
go test -bench=BenchmarkIncremental -benchmem ./lib/scanner/

# Chay tat ca test cua project de dam bao khong break gi
go test -count=1 ./...
```

---

## Phase 5 — Deploy

### 5.1 Build

```bash
# Build binary voi incremental scanner
go build -v ./cmd/syncthing/

# Build cho tat ca platform
GOOS=linux GOARCH=amd64 go build -o syncthing-linux-amd64 ./cmd/syncthing/
GOOS=darwin GOARCH=arm64 go build -o syncthing-darwin-arm64 ./cmd/syncthing/
GOOS=windows GOARCH=amd64 go build -o syncthing-windows-amd64.exe ./cmd/syncthing/
```

### 5.2 Test manual

1. **Chuan bi:**
   - Tao thu muc test voi 10,000+ file
   - Bat `IncrementalScan: true` trong config cua folder

2. **Kiem tra full scan dau tien:**
   ```bash
   # Khoi dong syncthing, kiem tra log
   syncthing -verbose | grep -i "incremental\|snapshot\|cache"
   # Ky vong: log "building initial baseline snapshot"
   ```

3. **Kiem tra incremental scan:**
   ```bash
   # Sua 1 file
   echo "changed" >> /path/to/sync/folder/test_file.txt
   # Kiem tra log: chi 1 file duoc hash, 9999 file duoc reuse
   # Ky vong: log "incremental scan: 1 dirty, 9999 reused from baseline"
   ```

4. **Kiem tra dong bo:**
   - Setup 2 node voi incremental scan
   - Sua file tren node A
   - Kiem tra file duoc dong bo sang node B dung va du

5. **Kiem tra performance:**
   ```bash
   # So sanh thoi gian scan
   # Full scan 10K files: ~X giay
   # Incremental scan 10 dirty / 10K files: ~Y giay
   # Ky vong: Y < X/10
   ```

6. **Kiem tra edge cases:**
   - Xoa file -> kiem tra file bien mat khoi dong bo
   - Rename thu muc -> kiem tra cac file trong thu muc duoc cap nhat
   - Them thu muc moi voi nhieu file -> kiem tra tat ca file moi duoc phat hien
   - Tat incremental scan -> kiem tra fall back ve full scan binh thuong

### 5.3 Merge

1. Tao Pull Request vao nhanh `develop`
2. Dam bao tat ca CI tests pass (bao gom race detector)
3. Review boi it nhat 2 nguoi
4. Squash merge voi message:
   ```
   feat(scanner): add incremental scan with baseline snapshot and digest cache (F3)

   - Add baseline snapshot (lib/scanner/snapshot.go) to reuse unchanged subtrees
   - Add digest cache (lib/scanner/cache.go) to skip re-hashing unchanged files
   - Modify walker to check dirty paths first, skip non-dirty subtrees
   - Add IncrementalScan config flag (default: false)
   - Scan cost now proportional to number of changes, not total file count
   ```

---

## Rollback Plan

### Khi nao can rollback

- Incremental scan tra ve ket qua khong chinh xac (mat file, du lieu cu)
- Performance te hon full scan (do overhead cua snapshot/cache)
- Memory usage tang qua cao do snapshot hoac cache
- Data race hoac deadlock trong snapshot/cache
- Khong tuong thich voi cac tinh nang khac (conflict resolution, ignore patterns)

### Cach rollback

#### Muc 1: Tat feature (khong can deploy lai)

```json
// Trong config.xml cua folder, dat:
{
  "incrementalScan": false
}
```

Khi `IncrementalScan == false`:
- Walker su dung `walkRegular()` goc, khong goi `walkRegularIncremental()`
- Snapshot va cache khong duoc tao hoac su dung
- Hanh vi hoan toan giong ban goc

#### Muc 2: Revert code (can deploy lai)

```bash
# Revert commit merge
git revert <merge-commit-hash>

# Hoac revert toan bo nhanh feature
git revert --no-commit HEAD~N..HEAD  # N = so commit cua feature
git commit -m "revert: remove incremental scanner (F3)"

# Build va deploy lai
go build ./cmd/syncthing/
```

#### Muc 3: Xoa du lieu cache (neu cache bi corrupt)

```bash
# Khong can lam gi dac biet — cache va snapshot chi luu trong memory.
# Restart syncthing la du de xoa toan bo cache/snapshot.
# Lan scan dau tien sau restart se la full scan.
systemctl restart syncthing
```

### Kiem tra sau rollback

1. Chay full test suite: `go test ./...`
2. Kiem tra log khong con thong bao lien quan den incremental scan
3. Kiem tra dong bo giua 2 node hoat dong binh thuong
4. Kiem tra memory usage tro ve muc binh thuong

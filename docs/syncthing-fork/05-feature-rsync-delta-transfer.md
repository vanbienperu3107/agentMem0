# F4 — rsync Delta Transfer

> **Muc tieu**: Thay the co che block exchange hien tai (truyen lai toan bo file khi block boundary thay doi) bang thuat toan rsync delta transfer, chi truyen nhung byte thuc su thay doi giua hai phien ban file.

---

## Phase 1 — Plan

### 1.1 Van de hien tai

Syncthing su dung co che **fixed-size block exchange** trong `lib/model/folder_sendrecv.go`:

- File duoc chia thanh cac block co dinh (128KB–16MB), moi block duoc hash bang SHA-256
- `copierRoutine` sao chep cac block co san local, `pullerRoutine` lay cac block thieu tu peer
- `finisherRoutine` xac nhan tat ca block va thay the file goc bang atomic rename
- Kich thuoc block duoc tinh boi `protocol.BlockSize()` nham dat ~2000 block/file

**Van de cot loi**: Khi chen 1 byte vao dau file, **tat ca block boundary deu bi dich** → toan bo file bi truyen lai, du chi co 1 byte thay doi. Voi file 1GB, thay doi 1 byte = truyen lai 1GB.

### 1.2 Giai phap: rsync Delta Transfer

Port thuat toan rsync tu Mutagen (`pkg/synchronization/rsync/`) vao Syncthing:

```
Receiver (co file cu)          Transmitter (co file moi)
       |                              |
       |--- Signature (rolling + strong hash) -->|
       |                              |
       |                    Deltify: so sanh file moi
       |                    voi signature, tim block trung
       |                              |
       |<-- Operations (data + block refs) ------|
       |                              |
    Patch: ghep data moi              |
    + block cu thanh file moi         |
```

### 1.3 Thiet ke ky thuat

#### Cau truc module moi: `lib/rsync/`

| File | Trach nhiem |
|------|-------------|
| `engine.go` | Signature generation, Deltify (tao delta ops), Patch (ap dung ops) |
| `rolling.go` | Rolling hash (weak hash) cho window truot |
| `operation.go` | Dinh nghia kieu Operation: data block vs reference block |

#### File can sua doi

| File | Thay doi |
|------|----------|
| `lib/model/folder_sendrecv.go` | Thay pipeline copier/puller bang rsync transmit/receive |
| `lib/protocol/protocol.go` | Them message type cho Signature va Operation exchange |

### 1.4 Thong so ky thuat

- **Block size**: `OptimalBlockSizeForBaseLength()` = `sqrt(24 * fileLength)`, clamp trong khoang `[1KB, 64KB]`
- **Weak hash**: Rolling checksum (Adler-32 variant), tinh toan O(1) khi truot window
- **Strong hash**: SHA-1 cho block matching (du an toan cho integrity check noi bo, khong dung cho bao mat)
- **Nguong kich hoat**: Chi dung rsync delta cho file >= 256KB. File nho hon van dung block exchange goc (overhead rsync khong dang)

### 1.5 Phan tich anh huong hieu nang

| Kich thuoc file | Thay doi | Hien tai (block exchange) | Sau (rsync delta) | Tiet kiem |
|-----------------|----------|---------------------------|---------------------|-----------|
| 100MB | 1 byte chen dau | ~100MB truyen | ~1KB truyen | ~99.999% |
| 100MB | 10KB sua o giua | ~100MB truyen | ~74KB truyen | ~99.93% |
| 100MB | 50% noi dung moi | ~100MB truyen | ~50MB truyen | ~50% |
| 1MB | 1 byte thay doi | ~1MB truyen | ~1KB truyen | ~99.9% |

---

## Phase 2 — Code

### 2.1 `lib/rsync/operation.go` — Dinh nghia kieu Operation

```go
// Package rsync implements the rsync delta transfer algorithm.
// Thay vi truyen toan bo file khi co thay doi nho, thuat toan nay
// chi truyen nhung byte thuc su khac biet giua hai phien ban.
package rsync

// OperationType phan biet giua hai loai operation trong delta stream.
type OperationType int

const (
	// OpBlock tham chieu den mot hoac nhieu block lien tiep tu file goc
	// ma van con nguyen ven trong file moi. Thay vi truyen lai noi dung,
	// chi can gui chi so block.
	OpBlock OperationType = iota

	// OpData chua noi dung moi (raw bytes) khong khop voi bat ky block
	// nao trong file goc. Day la phan thuc su can truyen qua mang.
	OpData
)

// Operation la don vi co ban cua rsync delta stream.
// Moi operation hoac la tham chieu den block cu (OpBlock),
// hoac la du lieu moi can truyen (OpData).
type Operation struct {
	Type OperationType

	// Chi dung khi Type == OpBlock:
	// BlockStart la chi so block dau tien trong file goc.
	// BlockCount la so block lien tiep khop (cho phep gop nhieu block
	// lien tiep thanh mot operation duy nhat, giam overhead).
	BlockStart uint64
	BlockCount uint64

	// Chi dung khi Type == OpData:
	// Data chua cac byte moi khong khop voi bat ky block nao.
	Data []byte
}

// BlockHash luu tru ca weak hash va strong hash cho mot block.
// Weak hash dung de loc nhanh (O(1) moi vi tri), strong hash dung
// de xac nhan chinh xac khi weak hash khop.
type BlockHash struct {
	// WeakHash la rolling checksum, tinh toan nhanh khi truot window.
	WeakHash uint32

	// StrongHash la SHA-1 cua noi dung block, dung khi weak hash match
	// de loai bo false positive.
	StrongHash [20]byte
}

// Signature chua metadata de so sanh hai phien ban file.
// Receiver tao Signature tu file cu va gui cho Transmitter.
type Signature struct {
	// BlockSize la kich thuoc moi block (byte), tru block cuoi.
	BlockSize uint64

	// LastBlockSize la kich thuoc block cuoi cung (co the nho hon BlockSize).
	LastBlockSize uint64

	// Hashes la danh sach hash cua tat ca block trong file goc,
	// theo thu tu tu dau den cuoi file.
	Hashes []BlockHash
}

// OptimalBlockSizeForBaseLength tinh kich thuoc block toi uu cho file
// co do dai cho truoc. Cong thuc: sqrt(24 * length), clamp [1KB, 64KB].
//
// Ly do: Block qua nho → nhieu hash, ton bo nho va bang thong cho signature.
// Block qua lon → kho tim match khi co chen/xoa nho.
// sqrt(24 * length) la diem can bang duoc chung minh trong ly thuyet rsync.
func OptimalBlockSizeForBaseLength(length uint64) uint64 {
	const (
		minBlockSize = 1024      // 1KB - khong nho hon de tranh signature qua lon
		maxBlockSize = 64 * 1024 // 64KB - khong lon hon de dam bao do nhay
	)

	if length == 0 {
		return minBlockSize
	}

	// sqrt(24 * length) - cong thuc tu ly thuyet rsync cua Tridgell
	optimal := uint64(0)
	product := 24 * length
	// Tinh sqrt bang Newton's method de tranh phu thuoc math.Sqrt (float64)
	x := product
	for {
		next := (x + product/x) / 2
		if next >= x {
			break
		}
		x = next
	}
	optimal = x

	// Clamp vao khoang cho phep
	if optimal < minBlockSize {
		return minBlockSize
	}
	if optimal > maxBlockSize {
		return maxBlockSize
	}
	return optimal
}
```

### 2.2 `lib/rsync/rolling.go` — Rolling Hash

```go
package rsync

// RollingHash implement rolling checksum (Adler-32 variant) cho rsync.
//
// Dac diem quan trong: khi truot window di 1 byte (bo byte cu, them byte moi),
// hash moi duoc tinh trong O(1) thay vi O(blockSize). Day la ly do
// rsync co the quet toan bo file hieu qua.
//
// Cong thuc:
//   a = sum(data[i]) mod M
//   b = sum((blockSize - i) * data[i]) mod M
//   weak = a + (b << 16)
//
// Khi truot: bo byte 'out', them byte 'in':
//   a' = a - out + in
//   b' = b - blockSize*out + a'
type RollingHash struct {
	a         uint16 // hash thanh phan 1: tong cac byte
	b         uint16 // hash thanh phan 2: tong co trong
	blockSize int    // kich thuoc window hien tai
	window    []byte // noi dung window hien tai (circular buffer)
	pos       int    // vi tri hien tai trong circular buffer
	count     int    // so byte da them vao window
}

// NewRollingHash tao rolling hash voi kich thuoc window cho truoc.
func NewRollingHash(blockSize int) *RollingHash {
	return &RollingHash{
		blockSize: blockSize,
		window:    make([]byte, blockSize),
	}
}

// Write tinh hash cho block du lieu ban dau (full window).
// Goi mot lan voi dung blockSize byte de khoi tao.
func (r *RollingHash) Write(data []byte) {
	r.a = 0
	r.b = 0
	r.count = len(data)
	r.pos = 0

	for i, v := range data {
		r.a += uint16(v)
		r.b += uint16(len(data)-i) * uint16(v)
		if i < r.blockSize {
			r.window[i] = v
		}
	}
}

// Roll truot window di 1 byte: bo 'out' (byte dau window cu),
// them 'in' (byte moi vao cuoi window).
// Tra ve hash moi trong O(1).
func (r *RollingHash) Roll(in byte) {
	out := r.window[r.pos]

	r.a += uint16(in) - uint16(out)
	r.b -= uint16(r.blockSize) * uint16(out)
	r.b += r.a

	r.window[r.pos] = in
	r.pos = (r.pos + 1) % r.blockSize
	r.count++
}

// Sum tra ve gia tri rolling hash hien tai (32-bit).
func (r *RollingHash) Sum() uint32 {
	return uint32(r.a) | (uint32(r.b) << 16)
}

// Reset dat lai trang thai hash ve ban dau.
func (r *RollingHash) Reset() {
	r.a = 0
	r.b = 0
	r.pos = 0
	r.count = 0
	for i := range r.window {
		r.window[i] = 0
	}
}
```

### 2.3 `lib/rsync/engine.go` — Core Engine

```go
package rsync

import (
	"crypto/sha1"
	"io"
)

// Engine la bo xu ly chinh cua rsync delta transfer.
// Moi Engine co internal buffer de tai su dung bo nho giua cac lan goi.
type Engine struct {
	buffer []byte // buffer noi bo, tai su dung de giam GC pressure
}

// NewEngine tao Engine moi voi buffer kich thuoc maxBlockSize.
func NewEngine(maxBlockSize int) *Engine {
	return &Engine{
		buffer: make([]byte, maxBlockSize),
	}
}

// GenerateSignature doc file goc va tao Signature chua hash cua tung block.
// Signature nay se duoc gui cho Transmitter de thuc hien Deltify.
//
// Quy trinh:
// 1. Tinh optimal block size dua tren kich thuoc file
// 2. Doc file theo tung block
// 3. Tinh weak hash (rolling) va strong hash (SHA-1) cho moi block
// 4. Tra ve Signature chua tat ca hash
func (e *Engine) GenerateSignature(reader io.Reader, fileSize uint64) (*Signature, error) {
	blockSize := OptimalBlockSizeForBaseLength(fileSize)

	sig := &Signature{
		BlockSize: blockSize,
	}

	buf := make([]byte, blockSize)
	var lastRead int

	for {
		n, err := io.ReadFull(reader, buf)
		if n > 0 {
			lastRead = n
			block := buf[:n]

			// Tinh weak hash
			rh := NewRollingHash(n)
			rh.Write(block)

			// Tinh strong hash
			strong := sha1.Sum(block)

			sig.Hashes = append(sig.Hashes, BlockHash{
				WeakHash:   rh.Sum(),
				StrongHash: strong,
			})
		}
		if err == io.EOF || err == io.ErrUnexpectedEOF {
			break
		}
		if err != nil {
			return nil, err
		}
	}

	if lastRead > 0 && uint64(lastRead) < blockSize {
		sig.LastBlockSize = uint64(lastRead)
	} else {
		sig.LastBlockSize = blockSize
	}

	return sig, nil
}

// Deltify so sanh file moi voi Signature cua file cu, tao ra danh sach
// Operation mo ta su khac biet.
//
// Day la buoc ton nhieu CPU nhat nhung tiet kiem nhieu bang thong nhat.
// Thuat toan:
// 1. Xay dung hash map tu weak hash → danh sach block index
// 2. Doc file moi, truot rolling hash qua tung byte
// 3. Khi weak hash khop → kiem tra strong hash
// 4. Neu strong hash khop → emit OpBlock (tham chieu block cu)
// 5. Neu khong khop → tich luy byte vao OpData
//
// Callback pattern: goi opHandler cho moi operation thay vi tich luy
// tat ca vao memory. Cho phep streaming operation qua mang.
func (e *Engine) Deltify(
	reader io.Reader,
	sig *Signature,
	opHandler func(Operation) error,
) error {
	if len(sig.Hashes) == 0 {
		// File goc rong, toan bo file moi la data moi
		return e.emitAllAsData(reader, opHandler)
	}

	// Buoc 1: Xay hash map weak → []blockIndex de lookup O(1)
	weakMap := make(map[uint32][]int, len(sig.Hashes))
	for i, h := range sig.Hashes {
		weakMap[h.WeakHash] = append(weakMap[h.WeakHash], i)
	}

	blockSize := int(sig.BlockSize)

	// Doc du lieu file moi vao buffer
	data, err := io.ReadAll(reader)
	if err != nil {
		return err
	}

	var pending []byte // byte chua khop, se thanh OpData
	pos := 0

	for pos <= len(data)-blockSize {
		// Tinh rolling hash cho window tai vi tri hien tai
		window := data[pos : pos+blockSize]
		rh := NewRollingHash(blockSize)
		rh.Write(window)
		weak := rh.Sum()

		matched := false
		if candidates, ok := weakMap[weak]; ok {
			// Weak hash khop — kiem tra strong hash de xac nhan
			strong := sha1.Sum(window)
			for _, idx := range candidates {
				if sig.Hashes[idx].StrongHash == strong {
					// Match! Flush pending data truoc
					if len(pending) > 0 {
						if err := opHandler(Operation{
							Type: OpData,
							Data: pending,
						}); err != nil {
							return err
						}
						pending = nil
					}
					// Emit block reference
					if err := opHandler(Operation{
						Type:       OpBlock,
						BlockStart: uint64(idx),
						BlockCount: 1,
					}); err != nil {
						return err
					}
					pos += blockSize
					matched = true
					break
				}
			}
		}

		if !matched {
			pending = append(pending, data[pos])
			pos++
		}
	}

	// Xu ly cac byte con lai cuoi file (khong du mot block)
	if pos < len(data) {
		pending = append(pending, data[pos:]...)
	}

	// Flush pending data cuoi cung
	if len(pending) > 0 {
		if err := opHandler(Operation{
			Type: OpData,
			Data: pending,
		}); err != nil {
			return err
		}
	}

	return nil
}

// Patch ap dung danh sach Operation len file goc de tao ra file moi.
//
// Voi moi operation:
// - OpData: ghi truc tiep raw bytes vao output
// - OpBlock: doc block tuong ung tu file goc (seek theo index * blockSize),
//   ghi vao output
//
// Ket qua: file output la ban sao chinh xac cua file moi phia transmitter.
func (e *Engine) Patch(
	base io.ReadSeeker,
	ops []Operation,
	blockSize uint64,
	output io.Writer,
) error {
	for _, op := range ops {
		switch op.Type {
		case OpData:
			if _, err := output.Write(op.Data); err != nil {
				return err
			}

		case OpBlock:
			for i := uint64(0); i < op.BlockCount; i++ {
				blockIdx := op.BlockStart + i
				offset := int64(blockIdx * blockSize)
				if _, err := base.Seek(offset, io.SeekStart); err != nil {
					return err
				}

				buf := make([]byte, blockSize)
				n, err := io.ReadFull(base, buf)
				if err == io.ErrUnexpectedEOF {
					// Block cuoi co the nho hon blockSize
					buf = buf[:n]
				} else if err != nil {
					return err
				}

				if _, err := output.Write(buf[:n]); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

// emitAllAsData doc toan bo reader va emit mot OpData duy nhat.
// Dung khi file goc rong (khong co block nao de tham chieu).
func (e *Engine) emitAllAsData(reader io.Reader, opHandler func(Operation) error) error {
	data, err := io.ReadAll(reader)
	if err != nil {
		return err
	}
	if len(data) > 0 {
		return opHandler(Operation{
			Type: OpData,
			Data: data,
		})
	}
	return nil
}
```

### 2.4 Sua doi `lib/protocol/protocol.go` — Them message type

```go
// Them vao danh sach MessageType hien co:

const (
	// ... cac message type hien tai ...

	// MessageTypeRsyncSignature duoc gui tu receiver → transmitter.
	// Chua Signature (block hashes) cua file cu phia receiver.
	MessageTypeRsyncSignature MessageType = 20

	// MessageTypeRsyncOperation duoc gui tu transmitter → receiver.
	// Chua stream cac Operation (delta) de receiver patch file cu
	// thanh file moi.
	MessageTypeRsyncOperation MessageType = 21

	// MessageTypeRsyncDone bao hieu ket thuc rsync delta stream.
	// Sau message nay, receiver co du du lieu de hoan tat Patch.
	MessageTypeRsyncDone MessageType = 22
)

// RsyncSignatureMessage chua signature cua file goc phia receiver.
type RsyncSignatureMessage struct {
	Folder    string    // ten folder dang dong bo
	Name      string    // ten file
	BlockSize uint64    // kich thuoc block
	Hashes    []BlockHashWire // danh sach hash, da serialize
}

// BlockHashWire la dang serialize cua BlockHash cho truyen qua mang.
type BlockHashWire struct {
	WeakHash   uint32
	StrongHash [20]byte
}

// RsyncOperationMessage chua mot batch Operation trong delta stream.
// Nhieu message nay co the duoc gui lien tiep cho mot file.
type RsyncOperationMessage struct {
	Folder     string
	Name       string
	Operations []OperationWire
}

// OperationWire la dang serialize cua Operation.
type OperationWire struct {
	Type       int32  // 0 = block, 1 = data
	BlockStart uint64 // chi dung cho OpBlock
	BlockCount uint64 // chi dung cho OpBlock
	Data       []byte // chi dung cho OpData
}
```

### 2.5 Sua doi `lib/model/folder_sendrecv.go` — Tich hop rsync

```go
// Them vao sendReceiveFolder struct:

import (
	"github.com/syncthing/syncthing/lib/rsync"
)

const (
	// rsyncMinFileSize la nguong kich thuoc file toi thieu de su dung
	// rsync delta. File nho hon dung block exchange truyen thong
	// vi overhead cua signature/deltify khong dang.
	rsyncMinFileSize = 256 * 1024 // 256KB
)

// rsyncTransfer thuc hien delta transfer cho mot file.
// Goi tu pullerRoutine khi phat hien file da ton tai local
// va kich thuoc >= rsyncMinFileSize.
//
// Quy trinh:
// 1. Mo file cu (local) va tao Signature
// 2. Gui Signature cho peer qua protocol
// 3. Peer chay Deltify va gui lai cac Operation
// 4. Nhan Operation va chay Patch de tao file moi
// 5. Verify checksum file moi
// 6. Atomic rename thay the file cu
func (f *sendReceiveFolder) rsyncTransfer(
	fi protocol.FileInfo,
	existingFile string,
	tempFile string,
) error {
	engine := rsync.NewEngine(64 * 1024)

	// Buoc 1: Mo file cu va tao signature
	base, err := os.Open(existingFile)
	if err != nil {
		// File cu khong doc duoc → fallback ve block exchange
		return errFallbackToBlockExchange
	}
	defer base.Close()

	stat, err := base.Stat()
	if err != nil {
		return errFallbackToBlockExchange
	}

	sig, err := engine.GenerateSignature(base, uint64(stat.Size()))
	if err != nil {
		return errFallbackToBlockExchange
	}

	// Buoc 2: Gui signature cho peer
	// (thong qua protocol message moi)
	sigMsg := &protocol.RsyncSignatureMessage{
		Folder:    f.folderID,
		Name:      fi.Name,
		BlockSize: sig.BlockSize,
		Hashes:    toWireHashes(sig.Hashes),
	}
	err = f.model.Connection(fi.DeviceID).SendRsyncSignature(sigMsg)
	if err != nil {
		return errFallbackToBlockExchange
	}

	// Buoc 3: Nhan operations tu peer
	ops, err := f.receiveRsyncOperations(fi)
	if err != nil {
		return errFallbackToBlockExchange
	}

	// Buoc 4: Patch file cu thanh file moi
	base.Seek(0, io.SeekStart) // reset reader ve dau file

	out, err := os.Create(tempFile)
	if err != nil {
		return err
	}
	defer out.Close()

	err = engine.Patch(base, ops, sig.BlockSize, out)
	if err != nil {
		os.Remove(tempFile)
		return errFallbackToBlockExchange
	}

	// Buoc 5: Verify checksum
	// (tai su dung co che verify hien co cua finisherRoutine)
	out.Seek(0, io.SeekStart)
	if !verifyFileHash(out, fi.Blocks) {
		os.Remove(tempFile)
		return errFallbackToBlockExchange
	}

	// Buoc 6: Atomic rename
	return osutil.RenameOrCopy(protocol.LocalFlags, tempFile, existingFile)
}

// shouldUseRsync quyet dinh co nen dung rsync delta cho file nay khong.
// Dieu kien:
// - File da ton tai o local (co ban cu de tao signature)
// - Kich thuoc file >= rsyncMinFileSize
// - Peer ho tro rsync protocol (version check)
func (f *sendReceiveFolder) shouldUseRsync(fi protocol.FileInfo) bool {
	if fi.Size < rsyncMinFileSize {
		return false
	}

	// Kiem tra file cu co ton tai khong
	localPath := filepath.Join(f.dir, fi.Name)
	if _, err := os.Stat(localPath); os.IsNotExist(err) {
		return false
	}

	// Kiem tra peer co ho tro rsync khong
	conn := f.model.Connection(fi.DeviceID)
	if conn == nil {
		return false
	}

	return conn.SupportsRsync()
}
```

---

## Phase 3 — Review Checklist

### 3.1 Correctness

| # | Hang muc | Kiem tra | Trang thai |
|---|----------|----------|------------|
| 1 | Rolling hash tinh toan dung | Unit test voi gia tri da biet | [ ] |
| 2 | Strong hash (SHA-1) khop voi Mutagen reference | So sanh output voi Mutagen test vectors | [ ] |
| 3 | Deltify tim dung cac block khop | Test voi file co chen/xoa/sua o nhieu vi tri | [ ] |
| 4 | Patch tao ra file giong het file moi | Byte-by-byte comparison sau Patch | [ ] |
| 5 | Block cuoi cung (kich thuoc khac) xu ly dung | Test voi file khong chia het cho blockSize | [ ] |
| 6 | File rong xu ly dung | Test ca hai truong hop: file cu rong, file moi rong | [ ] |
| 7 | Fallback ve block exchange khi rsync that bai | Simulate loi o moi buoc cua rsyncTransfer | [ ] |

### 3.2 Performance

| # | Hang muc | Kiem tra | Trang thai |
|---|----------|----------|------------|
| 8 | Deltify khong allocate qua nhieu bo nho | Profiling voi file 1GB | [ ] |
| 9 | Rolling hash O(1) moi buoc truot | Benchmark so voi tinh lai tu dau | [ ] |
| 10 | Signature size hop ly | Kiem tra: file 1GB → signature < 1MB | [ ] |
| 11 | Khong regression cho file nho (< 256KB) | Benchmark block exchange van hoat dong binh thuong | [ ] |

### 3.3 Protocol Compatibility

| # | Hang muc | Kiem tra | Trang thai |
|---|----------|----------|------------|
| 12 | Backward compatible voi peer cu | Peer cu khong ho tro rsync → tu dong dung block exchange | [ ] |
| 13 | Message type khong trung voi existing | Kiem tra MessageType 20, 21, 22 chua duoc dung | [ ] |
| 14 | Serialize/deserialize roundtrip dung | Test marshal → unmarshal cho moi message type | [ ] |

### 3.4 Security

| # | Hang muc | Kiem tra | Trang thai |
|---|----------|----------|------------|
| 15 | Khong su dung SHA-1 cho bao mat | Chi dung SHA-1 cho block matching, verify cuoi van dung SHA-256 | [ ] |
| 16 | Gioi han kich thuoc Operation.Data | Tranh OOM khi peer gui data block qua lon | [ ] |
| 17 | Validate block index trong Patch | BlockStart khong vuot qua so block trong file goc | [ ] |

---

## Phase 4 — Test

### 4.1 Unit Tests — `lib/rsync/`

#### Test 1: Rolling hash consistency

```go
// TestRollingHash_WriteVsRoll kiem tra rang tinh hash bang Write()
// cho ra ket qua giong voi tinh bang Roll() tung byte mot.
//
// TAI SAO: Rolling hash la nen tang cua Deltify. Neu Roll() cho ket qua
// khac Write(), Deltify se bo sot cac block khop → truyen du lieu thua.
// Day la bug tham lang (silent data overhead) rat kho phat hien trong production.
func TestRollingHash_WriteVsRoll(t *testing.T) {
	data := []byte("Hello, rsync delta transfer algorithm!")
	blockSize := 8

	// Cach 1: Write truc tiep voi window cuoi
	rh1 := NewRollingHash(blockSize)
	rh1.Write(data[len(data)-blockSize:])

	// Cach 2: Write window dau, roi Roll tung byte
	rh2 := NewRollingHash(blockSize)
	rh2.Write(data[:blockSize])
	for i := blockSize; i < len(data); i++ {
		rh2.Roll(data[i])
	}

	if rh1.Sum() != rh2.Sum() {
		t.Errorf("Write() = %d, Roll() = %d — rolling hash khong nhat quan",
			rh1.Sum(), rh2.Sum())
	}
}
```

#### Test 2: Signature round-trip

```go
// TestSignature_BlockCount kiem tra so luong block trong signature
// khop voi ky vong dua tren kich thuoc file va block size.
//
// TAI SAO: Sai so luong block se lam Deltify lookup sai index,
// dan den Patch ghep sai noi dung. Day la loi lam hong du lieu (data corruption)
// ma chi bieu hien khi file du lon de co nhieu block.
func TestSignature_BlockCount(t *testing.T) {
	tests := []struct {
		name     string
		fileSize int
		wantMin  int // so block toi thieu ky vong
		wantMax  int // so block toi da ky vong
	}{
		{"1KB file", 1024, 1, 1},
		{"10KB file", 10240, 1, 15},
		{"1MB file", 1024 * 1024, 15, 1024},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			data := make([]byte, tt.fileSize)
			for i := range data {
				data[i] = byte(i % 256)
			}

			engine := NewEngine(64 * 1024)
			sig, err := engine.GenerateSignature(
				bytes.NewReader(data),
				uint64(tt.fileSize),
			)
			if err != nil {
				t.Fatalf("GenerateSignature() error: %v", err)
			}

			got := len(sig.Hashes)
			if got < tt.wantMin || got > tt.wantMax {
				t.Errorf("block count = %d, want in [%d, %d]",
					got, tt.wantMin, tt.wantMax)
			}
		})
	}
}
```

#### Test 3: Deltify + Patch round-trip (no change)

```go
// TestDeltifyPatch_IdenticalFiles kiem tra khi file khong thay doi,
// Deltify chi tao OpBlock (khong co OpData) va Patch tai tao dung file.
//
// TAI SAO: Day la base case quan trong nhat. Neu ngay ca file khong doi
// ma van tao OpData, thi rsync delta khong tiet kiem gi so voi block exchange.
// Dong thoi kiem tra Patch co tai tao chinh xac file goc khong.
func TestDeltifyPatch_IdenticalFiles(t *testing.T) {
	original := bytes.Repeat([]byte("Syncthing rsync delta test! "), 1000)

	engine := NewEngine(64 * 1024)

	// Tao signature tu file goc
	sig, err := engine.GenerateSignature(
		bytes.NewReader(original),
		uint64(len(original)),
	)
	if err != nil {
		t.Fatalf("GenerateSignature: %v", err)
	}

	// Deltify file giong het → chi nen co OpBlock
	var ops []Operation
	var hasData bool
	err = engine.Deltify(
		bytes.NewReader(original),
		sig,
		func(op Operation) error {
			ops = append(ops, op)
			if op.Type == OpData {
				hasData = true
			}
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Deltify: %v", err)
	}

	if hasData {
		t.Error("Deltify tao OpData cho file giong het — khong nen co du lieu moi")
	}

	// Patch va so sanh
	var result bytes.Buffer
	err = engine.Patch(
		bytes.NewReader(original),
		ops,
		sig.BlockSize,
		&result,
	)
	if err != nil {
		t.Fatalf("Patch: %v", err)
	}

	if !bytes.Equal(result.Bytes(), original) {
		t.Error("Patch output khong khop voi file goc")
	}
}
```

#### Test 4: Deltify + Patch (1 byte insert at beginning)

```go
// TestDeltifyPatch_InsertAtStart kiem tra truong hop them 1 byte vao dau file.
//
// TAI SAO: Day chinh la van de cot loi ma rsync delta giai quyet.
// Block exchange hien tai se truyen lai TOAN BO file trong truong hop nay
// vi tat ca block boundary bi dich. rsync delta phai chi truyen ~1 byte data
// + cac block reference.
func TestDeltifyPatch_InsertAtStart(t *testing.T) {
	original := bytes.Repeat([]byte("ABCDEFGHIJKLMNOP"), 1000) // 16KB

	// Chen 1 byte 'X' vao dau
	modified := make([]byte, 0, len(original)+1)
	modified = append(modified, 'X')
	modified = append(modified, original...)

	engine := NewEngine(64 * 1024)

	sig, err := engine.GenerateSignature(
		bytes.NewReader(original),
		uint64(len(original)),
	)
	if err != nil {
		t.Fatalf("GenerateSignature: %v", err)
	}

	var ops []Operation
	var totalDataBytes int
	err = engine.Deltify(
		bytes.NewReader(modified),
		sig,
		func(op Operation) error {
			ops = append(ops, op)
			if op.Type == OpData {
				totalDataBytes += len(op.Data)
			}
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Deltify: %v", err)
	}

	// Data moi chi nen la vai byte (byte chen + padding),
	// khong phai toan bo file
	maxExpectedData := len(original) / 10 // toi da 10% file size
	if totalDataBytes > maxExpectedData {
		t.Errorf("OpData = %d bytes, muon < %d bytes — rsync khong hieu qua",
			totalDataBytes, maxExpectedData)
	}

	// Patch va verify
	var result bytes.Buffer
	err = engine.Patch(
		bytes.NewReader(original),
		ops,
		sig.BlockSize,
		&result,
	)
	if err != nil {
		t.Fatalf("Patch: %v", err)
	}

	if !bytes.Equal(result.Bytes(), modified) {
		t.Errorf("Patch output khong khop voi file da sua doi")
	}
}
```

#### Test 5: Edge case — file rong

```go
// TestDeltifyPatch_EmptyBase kiem tra khi file goc rong (file moi hoan toan).
//
// TAI SAO: Khi dong bo file moi (khong co ban cu), rsync phai fallback
// thanh truyen toan bo file dang OpData. Neu xu ly sai, Patch se tao
// file rong hoac crash.
func TestDeltifyPatch_EmptyBase(t *testing.T) {
	engine := NewEngine(64 * 1024)

	// Signature cua file rong
	sig, err := engine.GenerateSignature(
		bytes.NewReader(nil),
		0,
	)
	if err != nil {
		t.Fatalf("GenerateSignature: %v", err)
	}

	if len(sig.Hashes) != 0 {
		t.Errorf("File rong co %d hashes, muon 0", len(sig.Hashes))
	}

	// Deltify voi file moi co noi dung
	newContent := []byte("Noi dung file moi hoan toan")
	var ops []Operation
	err = engine.Deltify(
		bytes.NewReader(newContent),
		sig,
		func(op Operation) error {
			ops = append(ops, op)
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Deltify: %v", err)
	}

	// Phai co dung 1 OpData chua toan bo noi dung
	if len(ops) != 1 || ops[0].Type != OpData {
		t.Errorf("Mong 1 OpData, co %d operations", len(ops))
	}

	if !bytes.Equal(ops[0].Data, newContent) {
		t.Error("OpData khong chua day du noi dung file moi")
	}
}
```

#### Test 6: OptimalBlockSize bounds

```go
// TestOptimalBlockSize_Bounds kiem tra block size luon nam trong [1KB, 64KB].
//
// TAI SAO: Block size ngoai khoang cho phep gay ra nhieu van de:
// - Qua nho (< 1KB): signature qua lon, ton bo nho va bang thong
// - Qua lon (> 64KB): mat kha nang phat hien thay doi nho
// - Zero: chia cho 0 trong Deltify
func TestOptimalBlockSize_Bounds(t *testing.T) {
	tests := []uint64{
		0,              // file rong
		1,              // file 1 byte
		1024,           // 1KB
		1024 * 1024,    // 1MB
		1024 * 1024 * 1024, // 1GB
		10 * 1024 * 1024 * 1024, // 10GB
	}

	for _, size := range tests {
		bs := OptimalBlockSizeForBaseLength(size)
		if bs < 1024 {
			t.Errorf("size=%d: blockSize=%d < 1KB", size, bs)
		}
		if bs > 64*1024 {
			t.Errorf("size=%d: blockSize=%d > 64KB", size, bs)
		}
	}
}
```

### 4.2 Integration Tests

#### Test 7: rsync transfer end-to-end qua protocol

```go
// TestRsyncTransfer_E2E kiem tra toan bo flow rsync transfer
// giua hai peer thong qua protocol layer.
//
// TAI SAO: Unit test tung component khong dam bao chung hoat dong
// dung khi ghep lai. Test nay kiem tra: serialize Signature qua mang →
// Deltify phia transmitter → serialize Operations → Patch phia receiver
// → file cuoi cung dung.
func TestRsyncTransfer_E2E(t *testing.T) {
	// Setup: tao 2 folder, moi folder co 1 file
	// File ban dau giong nhau, sau do sua file phia transmitter
	// Ky vong: receiver nhan duoc ban moi chi voi delta transfer

	// ... (setup testbed voi 2 mock peer) ...

	// Verify: so sanh file sau dong bo, kiem tra byte count truyen < file size
}
```

#### Test 8: Fallback khi peer khong ho tro rsync

```go
// TestRsyncTransfer_FallbackOldPeer kiem tra rang khi peer khong ho tro
// rsync protocol, he thong tu dong fallback ve block exchange.
//
// TAI SAO: Trong qua trinh deploy, se co giai doan mot so peer da update
// (ho tro rsync) va mot so chua (chi biet block exchange). He thong PHAI
// hoat dong binh thuong trong giai doan chuyen tiep nay. Neu khong fallback,
// dong bo se that bai hoan toan voi peer cu.
func TestRsyncTransfer_FallbackOldPeer(t *testing.T) {
	// Setup: mock peer tra ve "unsupported message type" cho RsyncSignature
	// Ky vong: shouldUseRsync() = false, dung block exchange binh thuong
}
```

### 4.3 Benchmark Tests

```go
// BenchmarkDeltify_SmallChange do hieu nang Deltify khi file lon
// chi co thay doi nho. Day la use case pho bien nhat (edit van ban,
// config file, log append).
//
// TAI SAO: Deltify chay O(fileSize) vi phai truot qua toan bo file moi.
// Can dam bao thoi gian xu ly chap nhan duoc cho file lon (< 1 giay cho 100MB).
func BenchmarkDeltify_SmallChange(b *testing.B) {
	sizes := []int{
		1024 * 1024,       // 1MB
		10 * 1024 * 1024,  // 10MB
		100 * 1024 * 1024, // 100MB
	}

	for _, size := range sizes {
		b.Run(fmt.Sprintf("%dMB", size/(1024*1024)), func(b *testing.B) {
			original := make([]byte, size)
			rand.Read(original)

			modified := make([]byte, size)
			copy(modified, original)
			modified[size/2] = modified[size/2] ^ 0xFF // thay doi 1 byte o giua

			engine := NewEngine(64 * 1024)
			sig, _ := engine.GenerateSignature(
				bytes.NewReader(original),
				uint64(size),
			)

			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				engine.Deltify(
					bytes.NewReader(modified),
					sig,
					func(op Operation) error { return nil },
				)
			}
		})
	}
}
```

---

## Phase 5 — Deploy

### 5.1 Feature Flag

```go
// Them vao lib/config/folderconfiguration.go:

type FolderConfiguration struct {
	// ... cac field hien co ...

	// RsyncDeltaTransfer bat/tat rsync delta cho folder nay.
	// Mac dinh: false (tat) trong giai doan beta.
	// Khi da on dinh, se chuyen mac dinh thanh true.
	RsyncDeltaTransfer bool `xml:"rsyncDeltaTransfer" json:"rsyncDeltaTransfer" default:"false"`
}
```

### 5.2 Giai doan deploy

| Giai doan | Thoi gian | Hanh dong | Tieu chi chuyen tiep |
|-----------|-----------|-----------|----------------------|
| **Alpha** | Tuan 1–2 | Feature flag = `false` mac dinh. Developer tu bat trong config. | Unit test + integration test pass 100% |
| **Beta** | Tuan 3–4 | Feature flag van `false` mac dinh. Them UI toggle trong Advanced Settings. Gui ban beta cho ~100 nguoi dung tinh nguyen. | Khong co data corruption report trong 2 tuan |
| **GA** | Tuan 5–6 | Feature flag = `true` mac dinh cho file >= 256KB. Van cho phep tat trong config. | Benchmark cho thay tiet kiem >= 50% bang thong cho use case pho bien |
| **Cleanup** | Tuan 8+ | Xoa feature flag, rsync delta la mac dinh. Giu fallback ve block exchange. | 1 thang khong co bug report lien quan |

### 5.3 Monitoring

Them metrics moi de theo doi hieu qua rsync delta:

```go
// Metrics can theo doi:
var (
	// rsyncDeltaBytesTransferred do so byte thuc su truyen qua mang
	// khi dung rsync delta. So sanh voi rsyncDeltaFileSizeTotal
	// de tinh % tiet kiem.
	rsyncDeltaBytesTransferred = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "syncthing_rsync_delta_bytes_transferred_total",
			Help: "Total bytes transferred via rsync delta operations",
		},
	)

	// rsyncDeltaFileSizeTotal do tong kich thuoc file da dong bo
	// bang rsync delta. Ty le tiet kiem = 1 - (transferred/total).
	rsyncDeltaFileSizeTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "syncthing_rsync_delta_file_size_total",
			Help: "Total file sizes that used rsync delta",
		},
	)

	// rsyncDeltaFallbackCount dem so lan rsync that bai va fallback
	// ve block exchange. Neu tang dot bien → co bug can dieu tra.
	rsyncDeltaFallbackCount = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "syncthing_rsync_delta_fallback_total",
			Help: "Number of times rsync delta fell back to block exchange",
		},
	)

	// rsyncDeltaDuration do thoi gian xu ly rsync (signature + deltify + patch).
	// Neu qua cham so voi block exchange → can toi uu hoac dieu chinh nguong.
	rsyncDeltaDuration = prometheus.NewHistogram(
		prometheus.HistogramOpts{
			Name:    "syncthing_rsync_delta_duration_seconds",
			Help:    "Time spent on rsync delta transfer per file",
			Buckets: prometheus.ExponentialBuckets(0.001, 2, 15),
		},
	)
)
```

### 5.4 Alerting

| Metric | Nguong | Hanh dong |
|--------|--------|-----------|
| `rsyncDeltaFallbackCount` rate | > 10% tong so transfer | Dieu tra nguyen nhan fallback |
| `rsyncDeltaDuration` p99 | > 30 giay | Xem xet tang nguong `rsyncMinFileSize` |
| `rsyncDeltaBytesTransferred / rsyncDeltaFileSizeTotal` | > 0.9 (tiet kiem < 10%) | Kiem tra co dung rsync cho dung use case khong |

---

## Rollback Plan

### Khi nao rollback

- **Tu dong**: Khi `rsyncDeltaFallbackCount` rate > 50% trong 1 gio
- **Thu cong**: Khi phat hien data corruption hoac file sync sai noi dung
- **Khach hang bao cao**: Bat ky bao cao mat du lieu nao lien quan den rsync delta

### Cach rollback

#### Muc 1: Tat feature flag (khong can deploy)

```xml
<!-- Sua trong config.xml cua moi device: -->
<folder id="..." ...>
    <rsyncDeltaTransfer>false</rsyncDeltaTransfer>
</folder>
```

- **Thoi gian**: Tuc thi sau khi restart Syncthing
- **Anh huong**: Quay ve block exchange, khong mat du lieu
- **Khoi phuc**: Bat lai `rsyncDeltaTransfer` = `true` sau khi fix bug

#### Muc 2: Deploy lai phien ban cu (can deploy)

```bash
# Revert ve commit truoc khi merge rsync delta
git revert <rsync-delta-merge-commit>
# Build va deploy
go build ./cmd/syncthing
```

- **Thoi gian**: 15–30 phut (build + deploy)
- **Anh huong**: Mat toan bo tinh nang rsync delta
- **Khoi phuc**: Cherry-pick fix va deploy lai

#### Muc 3: Xu ly data corruption (truong hop xau nhat)

```bash
# 1. Tat rsync delta ngay lap tuc (muc 1)
# 2. Xac dinh cac file bi anh huong
syncthing cli show-rsync-transfers --since "2h ago" --status failed

# 3. Force re-sync cac file bi anh huong
syncthing cli reset-file --folder <folder-id> --file <path>

# 4. Verify integrity toan bo folder
syncthing cli verify-folder --folder <folder-id>
```

- **Thoi gian**: 1–4 gio tuy so luong file
- **Anh huong**: Cac file bi corruption se duoc tai lai hoan toan tu peer
- **Phong ngua**: Verify checksum (SHA-256) sau moi Patch ngan data corruption

### Ma tran quyet dinh rollback

| Tinh huong | Muc | Ly do |
|------------|-----|-------|
| Fallback rate cao nhung du lieu dung | 1 | Rsync khong hieu qua nhung khong nguy hiem |
| 1 user bao file sai noi dung | 1 + dieu tra | Co the la bug, can xac nhan truoc khi escalate |
| Nhieu user bao file sai noi dung | 2 | Revert code de ngan them thiet hai |
| Phat hien hash mismatch sau Patch | 2 | Bug trong Patch logic, can fix truoc khi bat lai |
| Du lieu mat khong khoi phuc duoc | 3 | Khoi phuc toan bo tu peer, audit code truoc khi bat lai |

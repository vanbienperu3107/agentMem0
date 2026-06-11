# F1 — WSS Transport

## Tổng quan

Thay thế raw TCP + TLS transport bằng WebSocket Secure (WSS) chạy trên HTTPS port 443. Giữ nguyên BEP protobuf messages — chỉ thay lớp transport bên ngoài.

**Branch:** `feature/wss-transport`
**Phụ thuộc:** Không (feature đầu tiên)
**Ảnh hưởng:** Transport layer, protocol framing, connection service

---

## Phase 1 — Plan

### 1.1 Files cần xóa

| File | Lý do |
|------|-------|
| `lib/connections/tcp_dial.go` | Raw TCP dialer — thay bằng WS dialer |
| `lib/connections/tcp_listen.go` | Raw TCP listener — thay bằng WS listener |
| `lib/connections/quic_dial.go` | QUIC transport — không cần |
| `lib/connections/quic_listen.go` | QUIC transport — không cần |
| `cmd/strelaysrv/` | Relay server — hub thay thế |
| `cmd/strelaypoolsrv/` | Relay pool — không cần |
| `cmd/stdiscosrv/` | Discovery server — hub registry thay thế |

### 1.2 Files cần thêm

| File | Mô tả |
|------|-------|
| `lib/connections/ws_dial.go` | WSS dialer — kết nối đến hub qua `wss://` |
| `lib/connections/ws_listen.go` | WSS listener — hub accept WSS connections |
| `lib/connections/ws_conn.go` | Wrapper: `websocket.Conn` → `net.Conn` interface |

### 1.3 Files cần sửa

| File | Thay đổi |
|------|----------|
| `lib/protocol/protocol.go` | `rawConnection`: bỏ custom framing (2B header + 4B msg len), dùng WS binary frames |
| `lib/connections/service.go` | Bỏ `tlsTimedHandshake()`, sửa connection setup flow |
| `lib/connections/registry.go` | Đăng ký WS dialer/listener thay TCP/QUIC |
| `lib/config/config.go` | Thêm `HubURL string` field |
| `go.mod` | Thêm `github.com/gorilla/websocket` |

### 1.4 Dependency mới

```bash
go get github.com/gorilla/websocket@v1.5.3
```

---

## Phase 2 — Code

### 2.1 ws_conn.go — WebSocket to net.Conn adapter

```go
// lib/connections/ws_conn.go
package connections

import (
    "io"
    "net"
    "sync"
    "time"

    "github.com/gorilla/websocket"
)

// wsConn wraps a *websocket.Conn to implement net.Conn.
// Syncthing's protocol layer expects a stream (net.Conn),
// but WebSocket is message-based. This adapter bridges the gap
// by reading one WS message at a time into a buffer.
type wsConn struct {
    ws     *websocket.Conn
    reader io.Reader
    mu     sync.Mutex
}

func newWSConn(ws *websocket.Conn) *wsConn {
    return &wsConn{ws: ws}
}

func (c *wsConn) Read(p []byte) (int, error) {
    c.mu.Lock()
    defer c.mu.Unlock()

    if c.reader == nil {
        _, r, err := c.ws.NextReader()
        if err != nil {
            return 0, err
        }
        c.reader = r
    }

    n, err := c.reader.Read(p)
    if err == io.EOF {
        // Current message consumed, reset for next message
        c.reader = nil
        if n > 0 {
            return n, nil
        }
        // Read next message immediately
        _, r, err := c.ws.NextReader()
        if err != nil {
            return 0, err
        }
        c.reader = r
        return c.reader.Read(p)
    }
    return n, err
}

func (c *wsConn) Write(p []byte) (int, error) {
    err := c.ws.WriteMessage(websocket.BinaryMessage, p)
    if err != nil {
        return 0, err
    }
    return len(p), nil
}

func (c *wsConn) Close() error                       { return c.ws.Close() }
func (c *wsConn) LocalAddr() net.Addr                { return c.ws.LocalAddr() }
func (c *wsConn) RemoteAddr() net.Addr               { return c.ws.RemoteAddr() }
func (c *wsConn) SetDeadline(t time.Time) error      { return c.ws.UnderlyingConn().SetDeadline(t) }
func (c *wsConn) SetReadDeadline(t time.Time) error  { return c.ws.UnderlyingConn().SetReadDeadline(t) }
func (c *wsConn) SetWriteDeadline(t time.Time) error { return c.ws.UnderlyingConn().SetWriteDeadline(t) }
```

### 2.2 ws_dial.go — WSS Dialer

```go
// lib/connections/ws_dial.go
package connections

import (
    "context"
    "crypto/tls"
    "net/http"
    "net/url"

    "github.com/gorilla/websocket"
)

type wsDialer struct {
    hubURL string // wss://hub.example.com/ws
    token  string // Bearer token (set sau khi F2 hoàn thành)
}

func newWSDialer(hubURL, token string) *wsDialer {
    return &wsDialer{hubURL: hubURL, token: token}
}

func (d *wsDialer) Dial(ctx context.Context, deviceID string) (*wsConn, error) {
    u, err := url.Parse(d.hubURL)
    if err != nil {
        return nil, err
    }

    header := http.Header{}
    if d.token != "" {
        header.Set("Authorization", "Bearer "+d.token)
    }
    // X-Device-ID header cho hub biết device nào đang kết nối
    header.Set("X-Device-ID", deviceID)

    dialer := websocket.Dialer{
        TLSClientConfig: &tls.Config{
            MinVersion: tls.VersionTLS13,
        },
        // Proxy support: gorilla/websocket tự đọc HTTP_PROXY/HTTPS_PROXY
        Proxy: http.ProxyFromEnvironment,
    }

    ws, _, err := dialer.DialContext(ctx, u.String(), header)
    if err != nil {
        return nil, err
    }

    // Set limits
    ws.SetReadLimit(500 << 20) // 500MB max message (match BEP max)

    return newWSConn(ws), nil
}
```

### 2.3 ws_listen.go — WSS Listener (Hub side)

```go
// lib/connections/ws_listen.go
package connections

import (
    "net"
    "net/http"
    "sync"

    "github.com/gorilla/websocket"
)

type wsListener struct {
    addr     string
    upgrader websocket.Upgrader
    connCh   chan *wsConn
    done     chan struct{}
    server   *http.Server
    mu       sync.Mutex
}

func newWSListener(addr string) *wsListener {
    l := &wsListener{
        addr:   addr,
        connCh: make(chan *wsConn, 64),
        done:   make(chan struct{}),
        upgrader: websocket.Upgrader{
            ReadBufferSize:  64 * 1024,
            WriteBufferSize: 64 * 1024,
            CheckOrigin:     func(r *http.Request) bool { return true },
        },
    }
    return l
}

func (l *wsListener) handleWS(w http.ResponseWriter, r *http.Request) {
    ws, err := l.upgrader.Upgrade(w, r, nil)
    if err != nil {
        return
    }
    ws.SetReadLimit(500 << 20)

    conn := newWSConn(ws)

    select {
    case l.connCh <- conn:
    case <-l.done:
        conn.Close()
    }
}

func (l *wsListener) Serve(tlsCertFile, tlsKeyFile string) error {
    mux := http.NewServeMux()
    mux.HandleFunc("/ws", l.handleWS)

    l.server = &http.Server{
        Addr:    l.addr,
        Handler: mux,
    }

    if tlsCertFile != "" {
        return l.server.ListenAndServeTLS(tlsCertFile, tlsKeyFile)
    }
    return l.server.ListenAndServe()
}

func (l *wsListener) Accept() (net.Conn, error) {
    select {
    case conn := <-l.connCh:
        return conn, nil
    case <-l.done:
        return nil, net.ErrClosed
    }
}

func (l *wsListener) Close() error {
    close(l.done)
    return l.server.Close()
}

func (l *wsListener) Addr() net.Addr {
    return &net.TCPAddr{} // placeholder
}
```

### 2.4 Sửa protocol.go — Bỏ custom framing

```go
// lib/protocol/protocol.go
// TRƯỚC (current):
func (c *rawConnection) readMessage() (Header, []byte, error) {
    // Read 2-byte header length
    // Read protobuf Header
    // Read 4-byte message length
    // Read protobuf Message
    // Decompress if needed
}

// SAU (proposed):
// WebSocket đã có framing (mỗi WS message = 1 BEP message).
// Chỉ cần read/write protobuf trực tiếp, không cần length prefix.
func (c *rawConnection) readMessage() (Header, []byte, error) {
    // Read full WS message (đã framed bởi WS layer)
    buf := make([]byte, 0, 65536)
    _, err := io.ReadFull(c.cr, buf) // cr = counting reader wrapping wsConn

    // Parse: first N bytes = Header protobuf, rest = Message protobuf
    // Giữ nguyên protobuf format bên trong
    // Chỉ bỏ length prefix vì WS đã handle

    // Cách đơn giản nhất: giữ nguyên framing format hiện tại.
    // wsConn.Read() sẽ seamlessly đọc từ WS messages.
    // Không cần sửa logic bên trong — chỉ transport layer thay đổi.
}

// GHI CHÚ QUAN TRỌNG:
// Vì wsConn implement net.Conn, và rawConnection đọc/ghi qua net.Conn,
// thực tế KHÔNG CẦN sửa readMessage/writeMessage.
// Custom framing (length prefix) vẫn hoạt động trên WS stream.
// Có thể optimize sau bằng cách dùng WS message boundaries,
// nhưng ban đầu giữ nguyên cho đơn giản — chỉ swap transport.
```

### 2.5 Sửa service.go — Connection setup

```go
// lib/connections/service.go
// Thay đổi chính:
// 1. Bỏ tlsTimedHandshake()
// 2. Bỏ TCP/QUIC registration
// 3. Thêm WS dialer/listener

// TRƯỚC:
func (s *service) connect(ctx context.Context) {
    // ... TCP dial + TLS handshake ...
    tc := tls.Client(conn, s.tlsCfg)
    tlsTimedHandshake(tc)
}

// SAU:
func (s *service) connect(ctx context.Context) {
    dialer := newWSDialer(s.cfg.HubURL(), s.token)
    conn, err := dialer.Dial(ctx, s.myID.String())
    if err != nil {
        // retry logic
        return
    }
    // conn đã là net.Conn, truyền thẳng vào protocol layer
    // Auth sẽ được handle ở HTTP header level (F2)
}
```

### 2.6 Sửa config — Thêm HubURL

```go
// lib/config/config.go
// Thêm vào struct Configuration:
type Configuration struct {
    // ... existing fields ...
    HubURL string `json:"hubURL" xml:"hubURL"`
}
```

---

## Phase 3 — Review Checklist

- [ ] `wsConn` implement đầy đủ `net.Conn` interface (6 methods)
- [ ] `wsConn.Read()` handle đúng message boundaries (không mất data giữa các WS messages)
- [ ] `wsConn.Write()` gửi mỗi `Write()` call là 1 WS binary message
- [ ] Dialer support proxy qua `http.ProxyFromEnvironment`
- [ ] Listener handle concurrent connections (buffered channel)
- [ ] TLS 1.3 enforced cho WSS connection
- [ ] Max message size = 500MB (match BEP limit)
- [ ] Graceful shutdown: listener close drains pending connections
- [ ] Không break existing protobuf message format
- [ ] `go vet ./lib/connections/...` pass
- [ ] `golangci-lint run ./lib/connections/...` pass

---

## Phase 4 — Test

### 4.1 Unit Tests

```go
// lib/connections/ws_conn_test.go
package connections

import (
    "net/http"
    "net/http/httptest"
    "testing"

    "github.com/gorilla/websocket"
)

// Test: wsConn implements net.Conn correctly
func TestWSConnReadWrite(t *testing.T) {
    // Setup: WS server echoes back messages
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        upgrader := websocket.Upgrader{}
        ws, err := upgrader.Upgrade(w, r, nil)
        if err != nil {
            t.Fatal(err)
        }
        defer ws.Close()
        for {
            mt, msg, err := ws.ReadMessage()
            if err != nil {
                return
            }
            ws.WriteMessage(mt, msg)
        }
    }))
    defer srv.Close()

    // Dial
    ws, _, err := websocket.DefaultDialer.Dial("ws://"+srv.Listener.Addr().String(), nil)
    if err != nil {
        t.Fatal(err)
    }
    conn := newWSConn(ws)
    defer conn.Close()

    // Write
    data := []byte("hello syncthing")
    n, err := conn.Write(data)
    if err != nil || n != len(data) {
        t.Fatalf("Write: n=%d err=%v", n, err)
    }

    // Read
    buf := make([]byte, 1024)
    n, err = conn.Read(buf)
    if err != nil {
        t.Fatalf("Read: err=%v", err)
    }
    if string(buf[:n]) != "hello syncthing" {
        t.Fatalf("got %q, want %q", buf[:n], data)
    }
}

// Test: multiple messages are read sequentially
func TestWSConnMultipleMessages(t *testing.T) {
    // Verify that reading across WS message boundaries works correctly.
    // Intent: wsConn.Read() phải tự động chuyển sang message tiếp theo
    // khi message hiện tại đã đọc hết.
    // ...
}

// Test: large messages (>64KB)
func TestWSConnLargeMessage(t *testing.T) {
    // Verify 500MB limit works (test with 1MB to keep CI fast).
    // Intent: đảm bảo không bị truncate ở message lớn.
    // ...
}

// Test: concurrent read/write
func TestWSConnConcurrent(t *testing.T) {
    // Verify goroutine safety.
    // Intent: wsConn phải safe khi read và write từ 2 goroutines khác nhau
    // (protocol layer đọc và ghi đồng thời).
    // ...
}
```

### 4.2 Integration Test

```go
// test/wss_integration_test.go
package integration

// Test: 2 syncthing instances sync qua WSS
func TestWSSSync(t *testing.T) {
    // 1. Start hub listener trên random port
    // 2. Start instance A kết nối hub
    // 3. Start instance B kết nối hub
    // 4. Tạo file trên A
    // 5. Verify file xuất hiện trên B trong < 10s
    // Intent: end-to-end WSS transport hoạt động đúng
}

// Test: WSS qua HTTP proxy
func TestWSSThroughProxy(t *testing.T) {
    // 1. Start simple HTTP proxy (net/http/httputil)
    // 2. Set HTTP_PROXY env
    // 3. Verify kết nối WSS đi qua proxy thành công
    // Intent: xác nhận proxy-friendly — đây là requirement chính
}

// Test: reconnect sau khi mất kết nối
func TestWSSReconnect(t *testing.T) {
    // 1. Connect A → hub
    // 2. Kill hub
    // 3. Restart hub
    // 4. Verify A tự reconnect
    // Intent: đảm bảo resilience
}
```

### 4.3 Mock Test

```go
// lib/connections/mock_ws_test.go

// mockWSConn cho unit test các component khác mà không cần real WS server
type mockWSConn struct {
    readBuf  *bytes.Buffer
    writeBuf *bytes.Buffer
}

func (m *mockWSConn) Read(p []byte) (int, error)  { return m.readBuf.Read(p) }
func (m *mockWSConn) Write(p []byte) (int, error) { return m.writeBuf.Write(p) }
// ... implement net.Conn interface ...
```

### 4.4 Chạy tests

```bash
# Unit
go test ./lib/connections/... -v -count=1 -run TestWSConn

# Integration
go test ./test/... -v -count=1 -run TestWSS -timeout 60s

# Race detector
go test ./lib/connections/... -race -count=1

# Benchmark
go test ./lib/connections/... -bench=BenchmarkWSConn -benchmem
```

---

## Phase 5 — Deploy

### 5.1 Build

```bash
git checkout feature/wss-transport
go run build.go -no-upgrade
```

### 5.2 Test thủ công

```bash
# Terminal 1: Hub (VPS)
./bin/syncthing --hub-mode --listen=:443 --tls-cert=cert.pem --tls-key=key.pem

# Terminal 2: Client A
./bin/syncthing --hub-url=wss://vps-ip:443/ws

# Terminal 3: Client B
./bin/syncthing --hub-url=wss://vps-ip:443/ws

# Tạo file, verify sync
echo "test" > ~/Sync/test.txt
# Check file xuất hiện ở client kia
```

### 5.3 Merge

```bash
git checkout develop
git merge feature/wss-transport
go test ./... -count=1  # verify không break gì
git push origin develop
```

---

## Rollback Plan

Nếu WSS transport có vấn đề nghiêm trọng:
1. Revert merge commit trên develop
2. Giữ `wsConn` adapter code (có thể reuse)
3. Fallback: dùng TCP + TLS qua stunnel/sslh trên port 443

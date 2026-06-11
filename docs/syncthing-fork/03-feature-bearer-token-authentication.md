# F2 — Bearer Token Authentication

## Tổng quan

Thay thế TLS client certificate authentication (DeviceID = SHA256(cert)) bằng JWT bearer token. Hub giữ shared secret, devices register và nhận token, mọi kết nối WSS gửi token trong HTTP header.

**Branch:** `feature/bearer-auth`
**Phụ thuộc:** F1 (WSS Transport) phải hoàn thành trước
**Ảnh hưởng:** Auth layer, connection service, API, config

---

## Phase 1 — Plan

### 1.1 Files cần xóa

| File | Lý do |
|------|-------|
| `lib/tlsutil/tlsutil.go` | Cert generation — không cần nữa |
| `lib/tlsutil/tlsutil_test.go` | Tests cho cert generation |

### 1.2 Files cần thêm

| File | Mô tả |
|------|-------|
| `lib/auth/token.go` | JWT generate, verify, refresh |
| `lib/auth/token_test.go` | Unit tests |
| `lib/auth/middleware.go` | HTTP middleware extract + verify token |
| `lib/auth/middleware_test.go` | Middleware tests |
| `lib/api/register.go` | `POST /api/register` endpoint |
| `lib/api/register_test.go` | Registration tests |

### 1.3 Files cần sửa

| File | Thay đổi |
|------|----------|
| `lib/protocol/deviceid.go` | DeviceID derive từ token payload thay vì cert fingerprint |
| `lib/connections/service.go` | `validateIdentity()` check token thay vì cert |
| `lib/connections/ws_dial.go` | Thêm token vào header khi dial |
| `lib/connections/ws_listen.go` | Extract + verify token từ upgrade request |
| `lib/config/config.go` | Thêm `HubSecret`, `DeviceToken` fields |
| `lib/api/api.go` | Thêm register route |

### 1.4 Dependencies mới

```bash
go get github.com/golang-jwt/jwt/v5@latest
```

---

## Phase 2 — Code

### 2.1 token.go — JWT Generate & Verify

```go
// lib/auth/token.go
package auth

import (
    "fmt"
    "time"

    "github.com/golang-jwt/jwt/v5"
)

// Claims chứa thông tin device trong JWT payload.
type Claims struct {
    DeviceID   string `json:"device_id"`
    DeviceName string `json:"device_name"`
    jwt.RegisteredClaims
}

// TokenService quản lý JWT lifecycle.
type TokenService struct {
    secret []byte        // HMAC-SHA256 signing key
    ttl    time.Duration // token lifetime
}

func NewTokenService(secret []byte, ttl time.Duration) *TokenService {
    return &TokenService{secret: secret, ttl: ttl}
}

// Generate tạo JWT cho device.
// deviceID: unique identifier (có thể UUID hoặc random string).
// deviceName: tên hiển thị do user đặt.
func (s *TokenService) Generate(deviceID, deviceName string) (string, error) {
    now := time.Now()
    claims := Claims{
        DeviceID:   deviceID,
        DeviceName: deviceName,
        RegisteredClaims: jwt.RegisteredClaims{
            IssuedAt:  jwt.NewNumericDate(now),
            ExpiresAt: jwt.NewNumericDate(now.Add(s.ttl)),
            Issuer:    "syncthing-hub",
        },
    }

    token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
    return token.SignedString(s.secret)
}

// Verify kiểm tra JWT signature và expiry, trả về Claims.
func (s *TokenService) Verify(tokenString string) (*Claims, error) {
    token, err := jwt.ParseWithClaims(tokenString, &Claims{}, func(t *jwt.Token) (interface{}, error) {
        if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
            return nil, fmt.Errorf("unexpected signing method: %v", t.Header["alg"])
        }
        return s.secret, nil
    })
    if err != nil {
        return nil, fmt.Errorf("invalid token: %w", err)
    }

    claims, ok := token.Claims.(*Claims)
    if !ok || !token.Valid {
        return nil, fmt.Errorf("invalid token claims")
    }
    return claims, nil
}

// Refresh tạo token mới từ token cũ (còn hạn).
func (s *TokenService) Refresh(tokenString string) (string, error) {
    claims, err := s.Verify(tokenString)
    if err != nil {
        return "", err
    }
    return s.Generate(claims.DeviceID, claims.DeviceName)
}
```

### 2.2 middleware.go — HTTP Auth Middleware

```go
// lib/auth/middleware.go
package auth

import (
    "context"
    "net/http"
    "strings"
)

type contextKey string

const ClaimsKey contextKey = "auth_claims"

// Middleware extracts Bearer token từ Authorization header,
// verify, và inject Claims vào request context.
func Middleware(ts *TokenService) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            authHeader := r.Header.Get("Authorization")
            if authHeader == "" {
                http.Error(w, "missing authorization header", http.StatusUnauthorized)
                return
            }

            parts := strings.SplitN(authHeader, " ", 2)
            if len(parts) != 2 || strings.ToLower(parts[0]) != "bearer" {
                http.Error(w, "invalid authorization format", http.StatusUnauthorized)
                return
            }

            claims, err := ts.Verify(parts[1])
            if err != nil {
                http.Error(w, "invalid token: "+err.Error(), http.StatusUnauthorized)
                return
            }

            ctx := context.WithValue(r.Context(), ClaimsKey, claims)
            next.ServeHTTP(w, r.WithContext(ctx))
        })
    }
}

// ClaimsFromContext lấy Claims từ request context.
func ClaimsFromContext(ctx context.Context) (*Claims, bool) {
    claims, ok := ctx.Value(ClaimsKey).(*Claims)
    return claims, ok
}
```

### 2.3 register.go — Device Registration API

```go
// lib/api/register.go
package api

import (
    "crypto/rand"
    "encoding/hex"
    "encoding/json"
    "net/http"

    "github.com/vanbienperu3107/syncthing/lib/auth"
)

type RegisterRequest struct {
    DeviceName string `json:"device_name"`
}

type RegisterResponse struct {
    DeviceID string `json:"device_id"`
    Token    string `json:"token"`
}

type TokenRefreshResponse struct {
    Token string `json:"token"`
}

// handleRegister xử lý POST /api/register.
// Tạo device ID mới (random 16 bytes hex) và trả JWT.
// NOTE: Endpoint này KHÔNG yêu cầu auth (bootstrap problem).
// Bảo vệ bằng registration secret hoặc rate limit.
func (s *apiService) handleRegister(w http.ResponseWriter, r *http.Request) {
    if r.Method != http.MethodPost {
        http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
        return
    }

    // Kiểm tra registration secret (optional, config)
    regSecret := r.Header.Get("X-Registration-Secret")
    if s.cfg.RegistrationSecret() != "" && regSecret != s.cfg.RegistrationSecret() {
        http.Error(w, "invalid registration secret", http.StatusForbidden)
        return
    }

    var req RegisterRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid request body", http.StatusBadRequest)
        return
    }
    if req.DeviceName == "" {
        http.Error(w, "device_name required", http.StatusBadRequest)
        return
    }

    // Generate random device ID
    idBytes := make([]byte, 16)
    if _, err := rand.Read(idBytes); err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    deviceID := hex.EncodeToString(idBytes)

    // Generate JWT
    token, err := s.tokenService.Generate(deviceID, req.DeviceName)
    if err != nil {
        http.Error(w, "failed to generate token", http.StatusInternalServerError)
        return
    }

    // Lưu device vào config
    s.cfg.AddDevice(deviceID, req.DeviceName)

    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(RegisterResponse{
        DeviceID: deviceID,
        Token:    token,
    })
}

// handleTokenRefresh xử lý POST /api/token/refresh.
// Yêu cầu bearer token hiện tại (còn hạn).
func (s *apiService) handleTokenRefresh(w http.ResponseWriter, r *http.Request) {
    if r.Method != http.MethodPost {
        http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
        return
    }

    claims, ok := auth.ClaimsFromContext(r.Context())
    if !ok {
        http.Error(w, "unauthorized", http.StatusUnauthorized)
        return
    }

    newToken, err := s.tokenService.Generate(claims.DeviceID, claims.DeviceName)
    if err != nil {
        http.Error(w, "failed to refresh token", http.StatusInternalServerError)
        return
    }

    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(TokenRefreshResponse{Token: newToken})
}
```

### 2.4 Sửa deviceid.go

```go
// lib/protocol/deviceid.go
// TRƯỚC:
// func NewDeviceID(rawCert []byte) DeviceID {
//     return DeviceID(sha256.Sum256(rawCert))
// }

// SAU:
// DeviceID giờ là string-based (hex 32 chars), không phải cert hash.
// Giữ type DeviceID nhưng đổi cách tạo.
type DeviceID string

func NewDeviceIDFromToken(claims *auth.Claims) DeviceID {
    return DeviceID(claims.DeviceID)
}
```

### 2.5 Sửa ws_listen.go — Token verify khi upgrade

```go
// lib/connections/ws_listen.go
// Thêm vào handleWS:
func (l *wsListener) handleWS(w http.ResponseWriter, r *http.Request) {
    // Verify token TRƯỚC khi upgrade WebSocket
    authHeader := r.Header.Get("Authorization")
    claims, err := l.tokenService.Verify(extractBearerToken(authHeader))
    if err != nil {
        http.Error(w, "unauthorized", http.StatusUnauthorized)
        return
    }

    ws, err := l.upgrader.Upgrade(w, r, nil)
    if err != nil {
        return
    }
    ws.SetReadLimit(500 << 20)

    conn := newWSConn(ws)
    conn.deviceID = claims.DeviceID // attach device ID to connection

    select {
    case l.connCh <- conn:
    case <-l.done:
        conn.Close()
    }
}
```

### 2.6 Sửa config — Thêm auth fields

```go
// lib/config/config.go
type Configuration struct {
    // ... existing fields ...
    HubURL             string `json:"hubURL" xml:"hubURL"`
    HubSecret          string `json:"hubSecret" xml:"hubSecret"`                   // HMAC key (hub only)
    DeviceToken        string `json:"deviceToken" xml:"deviceToken"`               // JWT (client only)
    RegistrationSecret string `json:"registrationSecret" xml:"registrationSecret"` // Optional
    TokenTTL           int    `json:"tokenTTL" xml:"tokenTTL"`                     // Hours, default 720 (30 days)
}
```

---

## Phase 3 — Review Checklist

- [ ] JWT signing dùng HS256 (HMAC-SHA256), không dùng none hoặc RS256
- [ ] Token verify kiểm tra cả signature VÀ expiry
- [ ] Registration endpoint có rate limit hoặc secret protection
- [ ] Token không chứa sensitive data (chỉ device_id, device_name, exp)
- [ ] Hub secret đủ dài (>= 32 bytes random)
- [ ] Token refresh chỉ chấp nhận token chưa hết hạn
- [ ] middleware reject token với signing method không mong đợi
- [ ] Config file permissions: hub secret không world-readable
- [ ] Không log token value (chỉ log device_id)
- [ ] `go vet ./lib/auth/...` pass
- [ ] `golangci-lint run ./lib/auth/...` pass

---

## Phase 4 — Test

### 4.1 Unit Tests

```go
// lib/auth/token_test.go
package auth

import (
    "testing"
    "time"
)

func TestGenerateAndVerify(t *testing.T) {
    ts := NewTokenService([]byte("test-secret-32-bytes-long!!!!!!"), 24*time.Hour)

    token, err := ts.Generate("device-123", "My Laptop")
    if err != nil {
        t.Fatal(err)
    }

    claims, err := ts.Verify(token)
    if err != nil {
        t.Fatal(err)
    }

    if claims.DeviceID != "device-123" {
        t.Errorf("got device_id=%q, want %q", claims.DeviceID, "device-123")
    }
    if claims.DeviceName != "My Laptop" {
        t.Errorf("got device_name=%q, want %q", claims.DeviceName, "My Laptop")
    }
}

// Intent: expired token phải bị reject
func TestExpiredTokenRejected(t *testing.T) {
    ts := NewTokenService([]byte("test-secret-32-bytes-long!!!!!!"), 1*time.Millisecond)
    token, _ := ts.Generate("device-123", "test")
    time.Sleep(10 * time.Millisecond)

    _, err := ts.Verify(token)
    if err == nil {
        t.Fatal("expected error for expired token")
    }
}

// Intent: token signed với key khác phải bị reject
func TestWrongSecretRejected(t *testing.T) {
    ts1 := NewTokenService([]byte("secret-aaaaaaaaaaaaaaaaaaaaaaaa"), 24*time.Hour)
    ts2 := NewTokenService([]byte("secret-bbbbbbbbbbbbbbbbbbbbbbbb"), 24*time.Hour)

    token, _ := ts1.Generate("device-123", "test")
    _, err := ts2.Verify(token)
    if err == nil {
        t.Fatal("expected error for wrong secret")
    }
}

// Intent: tampered token phải bị reject
func TestTamperedTokenRejected(t *testing.T) {
    ts := NewTokenService([]byte("test-secret-32-bytes-long!!!!!!"), 24*time.Hour)
    token, _ := ts.Generate("device-123", "test")
    // Flip 1 char in payload
    tampered := token[:len(token)/2] + "X" + token[len(token)/2+1:]
    _, err := ts.Verify(tampered)
    if err == nil {
        t.Fatal("expected error for tampered token")
    }
}

func TestRefresh(t *testing.T) {
    ts := NewTokenService([]byte("test-secret-32-bytes-long!!!!!!"), 24*time.Hour)
    token1, _ := ts.Generate("device-123", "test")
    token2, err := ts.Refresh(token1)
    if err != nil {
        t.Fatal(err)
    }
    if token1 == token2 {
        t.Fatal("refreshed token should differ (different iat/exp)")
    }
    claims, _ := ts.Verify(token2)
    if claims.DeviceID != "device-123" {
        t.Fatal("device_id should be preserved after refresh")
    }
}
```

### 4.2 Middleware Test

```go
// lib/auth/middleware_test.go
package auth

import (
    "net/http"
    "net/http/httptest"
    "testing"
    "time"
)

// Intent: valid token → handler invoked, claims in context
func TestMiddlewareValidToken(t *testing.T) {
    ts := NewTokenService([]byte("test-secret-32-bytes-long!!!!!!"), 24*time.Hour)
    token, _ := ts.Generate("dev-1", "test")

    handler := Middleware(ts)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        claims, ok := ClaimsFromContext(r.Context())
        if !ok || claims.DeviceID != "dev-1" {
            t.Error("expected claims in context")
        }
        w.WriteHeader(http.StatusOK)
    }))

    req := httptest.NewRequest("GET", "/", nil)
    req.Header.Set("Authorization", "Bearer "+token)
    rr := httptest.NewRecorder()
    handler.ServeHTTP(rr, req)

    if rr.Code != 200 {
        t.Errorf("got status %d, want 200", rr.Code)
    }
}

// Intent: missing header → 401
func TestMiddlewareNoHeader(t *testing.T) {
    ts := NewTokenService([]byte("secret"), 24*time.Hour)
    handler := Middleware(ts)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))

    req := httptest.NewRequest("GET", "/", nil)
    rr := httptest.NewRecorder()
    handler.ServeHTTP(rr, req)

    if rr.Code != 401 {
        t.Errorf("got status %d, want 401", rr.Code)
    }
}
```

### 4.3 Integration Test

```go
// test/auth_integration_test.go

// Intent: full flow register → connect → sync
func TestAuthFullFlow(t *testing.T) {
    // 1. Start hub
    // 2. POST /api/register → get token
    // 3. Connect WSS with token
    // 4. Verify connection accepted
    // 5. Try connect with invalid token → verify rejected
}
```

### 4.4 Chạy tests

```bash
go test ./lib/auth/... -v -count=1
go test ./lib/api/... -v -count=1 -run TestRegister
go test ./test/... -v -count=1 -run TestAuth
```

---

## Phase 5 — Deploy

### 5.1 Config hub (VPS)

```yaml
# config.yaml trên VPS
hubURL: "wss://0.0.0.0:443/ws"
hubSecret: "random-64-byte-hex-string-generate-with-openssl-rand-hex-32"
registrationSecret: "another-secret-for-device-registration"
tokenTTL: 720  # 30 days
```

### 5.2 Register device

```bash
# Từ máy client
curl -X POST https://vps-ip:443/api/register \
  -H "X-Registration-Secret: another-secret-for-device-registration" \
  -H "Content-Type: application/json" \
  -d '{"device_name": "My Laptop"}'

# Response:
# {"device_id": "a1b2c3d4...", "token": "eyJhbGciOiJIUzI1NiIs..."}
```

### 5.3 Config client

```yaml
# config.yaml trên client
hubURL: "wss://vps-ip:443/ws"
deviceToken: "eyJhbGciOiJIUzI1NiIs..."
```

### 5.4 Merge

```bash
git checkout develop
git merge feature/bearer-auth
go test ./... -count=1
git push origin develop
```

---

## Rollback Plan

Nếu bearer auth có vấn đề:
1. Revert merge commit
2. Fallback: dùng mutual TLS qua WSS (WS over TLS with client certs) — vẫn trên port 443

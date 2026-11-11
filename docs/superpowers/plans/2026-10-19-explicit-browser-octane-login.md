# Explicit Browser Octane Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit browser-based BMW SSO login command that caches an authenticated Octane session per dashboard user without changing password-login behavior.

**Architecture:** The page defaults to session keeper Mobile Push using the entered BMW account and strong-auth PIN. If the user disables Mobile Push, the same login command launches a visible browser, fills the entered credentials only into that browser, waits for BMW SSO/2FA, validates an Octane API Cookie, and stores a cloneable `requests.Session` in a process-local user cache.

**Tech Stack:** React/TypeScript, FastAPI, Starlette threadpool, Playwright sync API, requests, pytest, Vitest.

---

### Task 1: Manual Cookie Capture Helper

**Files:**
- Modify: `backend/analytics/ingest/playwright_cookie_manager.py`
- Test: `backend/tests/test_octane_auth.py`

- [ ] **Step 1: Write the failing test**

```python
def test_interactive_cookie_capture_uses_explicit_login_credentials(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(playwright_cookie_manager, "_run_interactive_cookie_capture", lambda **kwargs: captured.update(kwargs) or "JSESSIONID=abc")

    result = playwright_cookie_manager.capture_interactive_cookie_file(
        base_url="https://octane-prod.bmwgroup.net",
        cookie_file=tmp_path / "cookie.txt",
        username="q123456",
        password="tss-password",
    )

    assert result == "JSESSIONID=abc"
    assert captured["base_url"] == "https://octane-prod.bmwgroup.net"
    assert captured["username"] == "q123456"
    assert captured["password"] == "tss-password"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_octane_auth.py::test_interactive_cookie_capture_does_not_submit_login_credentials -q`

Expected: FAIL because `capture_interactive_cookie_file` is undefined.

- [ ] **Step 3: Write minimal implementation**

```python
def capture_interactive_cookie_file(*, base_url: str, cookie_file: Path) -> str:
    cookie_header = _run_interactive_cookie_capture(base_url=base_url)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(cookie_header, encoding="utf-8")
    return cookie_header
```

`_run_interactive_cookie_capture()` opens Playwright visibly, calls `_try_submit_login()` only with the credentials from this explicit request, waits for the user to finish any remaining SSO/2FA step, probes the teams API, and returns only a validated Octane Cookie header.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_octane_auth.py::test_interactive_cookie_capture_does_not_submit_login_credentials -q`

Expected: PASS.

### Task 2: Browser Session Cache and API Route

**Files:**
- Modify: `backend/analytics/auth_routes.py`
- Modify: `backend/tests/test_auth_api_routes.py`

- [ ] **Step 1: Write the failing route test**

```python
def test_browser_login_issues_dashboard_session_and_caches_octane_session(monkeypatch):
    browser_session = object()
    monkeypatch.setattr(auth_routes, "_create_interactive_browser_session", lambda username: browser_session)

    with TestClient(app) as client:
        response = client.post("/api/auth/browser-login", json={"username": "q123456"})

    assert response.status_code == 200
    assert response.json() == {"username": "q123456", "authenticated": True}
    assert "vizion_session=" in response.headers["set-cookie"]
    assert auth_routes.get_user_octane_session("q123456") is browser_session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest backend/tests/test_auth_api_routes.py::test_browser_login_issues_dashboard_session_and_caches_octane_session -q`

Expected: FAIL with route or helper missing.

- [ ] **Step 3: Write minimal implementation**

```python
@router.post("/api/auth/browser-login")
async def browser_login(body: BrowserLoginRequest) -> JSONResponse:
    username = body.username.strip()
    session = await run_in_threadpool(_create_interactive_browser_session, username, body.password)
    if session is None:
        raise HTTPException(status_code=401, detail="浏览器登录未完成或未获取到 Octane 会话")
    _cache_browser_session(username, session)
    return _issue_dashboard_login_response(username)
```

Use a lock-protected in-memory cache, return cloned sessions for downstream Octane requests, and clear that cache on logout. Do not read `login_info.txt`, call `refresh_cookie_file`, or call the password keeper from this route.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest backend/tests/test_auth_api_routes.py::test_browser_login_issues_dashboard_session_and_caches_octane_session -q`

Expected: PASS.

### Task 3: Single Browser-Backed Login Command

**Files:**
- Modify: `src/lib/auth.ts`
- Modify: `src/pages/Login.tsx`
- Modify: `src/test/auth.test.ts`

- [ ] **Step 1: Write the failing client test**

```ts
it("starts visible browser login with the entered credentials", async () => {
  fetchMock.mockResolvedValue({ ok: true });

  await login("q123456", "tss-password");

  expect(fetchMock).toHaveBeenCalledWith("/api/auth/browser-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ username: "q123456", password: "tss-password" }),
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/test/auth.test.ts`

Expected: FAIL because `login` still posts to `/api/auth/login`.

- [ ] **Step 3: Write minimal implementation**

```ts
export async function login(username: string, password: string): Promise<LoginResult> {
  return postLogin("/api/auth/browser-login", { username, password });
}
```

Keep one `登录` button in `Login.tsx`. It requires the account and password fields, shows a waiting state while the visible browser is open, and uses the existing successful refresh-and-navigate flow after browser authentication completes.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/test/auth.test.ts`

Expected: PASS.

### Task 4: Verification

**Files:**
- Test: `backend/tests/test_auth_api_routes.py`
- Test: `backend/tests/test_auth_routes.py`
- Test: `backend/tests/test_octane_auth.py`
- Test: `src/test/auth.test.ts`

- [ ] **Step 1: Run focused test suites**

Run: `python -m pytest backend/tests/test_auth_api_routes.py backend/tests/test_auth_routes.py backend/tests/test_octane_auth.py -q`

Expected: PASS.

- [ ] **Step 2: Build frontend**

Run: `npm run build`

Expected: exit code 0.

- [ ] **Step 3: Start local services and verify non-secret routes**

Run: `npm run dev`

Expected: `3003`, `3004`, and `8080` are listening; `GET /api/auth/me` returns `{"authenticated":false}` before login.
// login.js — bootstrap for /login. Posts the shared staff password to
// /api/auth/login; on success the server has already set the signed
// session cookie (HttpOnly, so this script never sees or handles the
// cookie value itself) — we just redirect to `next` (or `/`).
const form = document.getElementById("login-form");
const errorEl = document.getElementById("login-error");
const passwordInput = document.getElementById("password");

// Open-redirect guard: only ever follow a `next` that is a same-site path
// starting with "/" (never an absolute URL to another host).
function getSafeNext() {
  const raw = new URLSearchParams(window.location.search).get("next");
  if (raw && raw.startsWith("/") && !raw.startsWith("//")) return raw;
  return "/";
}

function showError(message) {
  errorEl.textContent = message;
  passwordInput.value = "";
  passwordInput.focus();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.textContent = "";
  const password = passwordInput.value;
  if (!password) {
    showError("パスワードを入力してください");
    return;
  }
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (res.ok) {
      window.location.href = getSafeNext();
      return;
    }
    if (res.status === 429) {
      showError("試行回数が多すぎます。しばらくしてから再度お試しください。");
    } else if (res.status === 503) {
      showError("現在ログインできません。管理者にご連絡ください。");
    } else {
      showError("パスワードが違います");
    }
  } catch (e) {
    showError("通信エラーが発生しました。もう一度お試しください。");
  }
});

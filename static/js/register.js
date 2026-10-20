"use strict";

const form = document.getElementById("register-form");
const message = document.getElementById("message");
const password = document.getElementById("register-password");
const confirmation = document.getElementById("register-confirm-password");
const matchMessage = document.getElementById("password-match-message");

function updatePasswordMatch() {
  // Keep the form valid while the user is still typing the confirmation.
  // Show a mismatch only after the field contains something.
  const mismatch = confirmation.value.length > 0 && password.value !== confirmation.value;
  confirmation.setCustomValidity(mismatch ? "Passwords do not match." : "");
  matchMessage.textContent = mismatch ? "Passwords do not match." :
    (confirmation.value && password.value === confirmation.value ? "Passwords match." : "");
  matchMessage.classList.toggle("field-error", mismatch);
  matchMessage.classList.toggle("field-ok", !mismatch && Boolean(confirmation.value));
}

password.addEventListener("input", updatePasswordMatch);
confirmation.addEventListener("input", updatePasswordMatch);

for (const button of document.querySelectorAll("[data-password-target]")) {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.passwordTarget);
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    button.textContent = visible ? "Hide" : "Show";
    button.setAttribute("aria-pressed", String(visible));
    button.setAttribute("aria-label", `${visible ? "Hide" : "Show"} ${input.id === "register-password" ? "password" : "confirmed password"}`);
  });
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  updatePasswordMatch();
  if (!form.reportValidity()) return;

  const button = form.querySelector('[type="submit"]');
  const payload = Object.fromEntries(new FormData(form));
  message.className = "notice hidden";
  button.disabled = true;
  button.textContent = "Submitting request...";

  try {
    const response = await fetch("/api/auth/register", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : "Unable to submit your request.");

    form.reset();
    form.classList.add("hidden");
    message.className = "notice hidden";
    const success = document.getElementById("registration-success");
    const responseMessage = typeof data.message === "string" ? data.message : "An administrator must approve access before sign-in.";
    document.getElementById("success-message").textContent =
      responseMessage.replace(/^Request received\.\s*/i, "");
    document.querySelector("body[data-page='register'] .auth-alt").classList.add("hidden");
    success.classList.remove("hidden");
    success.focus();
  } catch (error) {
    message.textContent = error instanceof Error ? error.message : "Unable to submit your request.";
    message.className = "notice error";
    message.scrollIntoView({ block: "nearest" });
  } finally {
    button.disabled = false;
    button.textContent = "Submit account request";
  }
});

(() => {
  const mount = document.querySelector("[data-owner-auth]");
  const status = document.querySelector("[data-auth-status]");
  const signOut = document.querySelector("[data-owner-sign-out]");
  const ownerLink = document.getElementById("owner-access");
  if (!mount && !ownerLink) return;

  const showError = (message) => {
    if (!status) return;
    status.textContent = message;
    status.classList.add("error");
  };

  async function loadClerk() {
    const response = await fetch("/api/owner-auth/config", { credentials: "same-origin" });
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || "Owner sign-in is not available right now.");
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.async = true;
      script.crossOrigin = "anonymous";
      script.src = `${config.proxyUrl || `https://${config.frontendApiHost}`}/npm/@clerk/clerk-js@latest/dist/clerk.browser.js`;
      script.dataset.clerkPublishableKey = config.publishableKey;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Could not load secure sign-in."));
      document.head.append(script);
    });
    await window.Clerk.load({
      publishableKey: config.publishableKey,
      proxyUrl: config.proxyUrl || undefined,
    });
    return config;
  }

  function signOutButton() {
    if (!signOut || signOut.dataset.bound) return;
    signOut.dataset.bound = "true";
    signOut.hidden = false;
    signOut.addEventListener("click", () => window.Clerk.signOut({ redirectUrl: "/" }));
  }

  async function startSignInPage() {
    try {
      await loadClerk();
      const isSignUp = document.body.dataset.authPage === "sign-up";
      const appearance = {
        options: {
          logoPlacement: "inside",
          logoLinkUrl: `${window.location.origin}/`,
          logoImageUrl: `${window.location.origin}/favicon.svg`,
          socialButtonsPlacement: "top",
          socialButtonsVariant: "blockButton",
        },
        variables: {
          colorPrimary: "#d8bc86",
          colorForeground: "#f2eee5",
          colorMutedForeground: "#aaa69e",
          colorDanger: "#f0a28f",
          colorBackground: "#111110",
          colorInput: "#191816",
          colorInputForeground: "#f2eee5",
          colorNeutral: "#605c55",
          borderRadius: "4px",
          fontFamily: "Inter, sans-serif",
        },
        elements: {
          rootBox: { width: "100%", display: "flex", justifyContent: "center" },
          cardBox: {
            width: "440px",
            maxWidth: "100%",
            backgroundColor: "#111110",
            border: "1px solid #302e29",
            borderRadius: "8px",
            boxShadow: "none",
            overflow: "hidden",
          },
          card: { backgroundColor: "transparent", border: "none", boxShadow: "none" },
          footer: { backgroundColor: "transparent", border: "none", boxShadow: "none" },
          headerTitle: { color: "#f2eee5", fontFamily: "Playfair Display, Georgia, serif" },
          headerSubtitle: { color: "#aaa69e" },
          socialButtonsBlockButtonText: { color: "#f2eee5" },
          formFieldLabel: { color: "#e8dcc3" },
          footerActionLink: { color: "#d8bc86" },
          footerActionText: { color: "#aaa69e" },
          dividerText: { color: "#aaa69e" },
          identityPreviewEditButton: { color: "#d8bc86" },
          formFieldSuccessText: { color: "#c1d4b2" },
          alertText: { color: "#f2eee5" },
          logoBox: { backgroundColor: "transparent" },
          socialButtonsBlockButton: { backgroundColor: "#191816", borderColor: "#605c55" },
          formButtonPrimary: { color: "#111110", fontWeight: "600" },
          formFieldInput: { color: "#f2eee5", backgroundColor: "#191816", borderColor: "#605c55" },
          footerAction: { backgroundColor: "transparent" },
          dividerLine: { backgroundColor: "#38352f" },
          alert: { backgroundColor: "#2a1b18", borderColor: "#754b40" },
          otpCodeFieldInput: { color: "#f2eee5", backgroundColor: "#191816" },
          formFieldRow: { color: "#e8dcc3" },
          main: { backgroundColor: "transparent" },
        },
      };
      if (isSignUp) {
        window.Clerk.mountSignUp(mount, {
          routing: "path",
          path: "/sign-up",
          signInUrl: "/sign-in",
          forceRedirectUrl: "/",
          appearance,
        });
      } else {
        window.Clerk.mountSignIn(mount, {
          routing: "path",
          path: "/sign-in",
          signUpUrl: "/sign-up",
          forceRedirectUrl: "/",
          appearance,
        });
      }
      status.textContent = "";
      let currentIdentity = identitySignature(window.Clerk.user);
      const updateAccount = ({ user }) => {
        const nextIdentity = identitySignature(user);
        if (nextIdentity !== currentIdentity) {
          window.location.reload();
          return;
        }
        if (!user) {
          if (signOut) signOut.hidden = true;
          status.textContent = "";
          status.classList.remove("owner");
          return;
        }
        signOutButton();
        fetch("/api/owner-auth/session", { credentials: "same-origin" })
          .then((res) => res.json())
          .then((session) => {
            if (identitySignature(window.Clerk.user) !== currentIdentity) return;
            status.textContent = session.is_owner
              ? "Owner access is active. Both creation limits are lifted for your account."
              : "Signed in. This account does not have app-owner access.";
            status.classList.toggle("owner", Boolean(session.is_owner));
          })
          .catch(() => { status.textContent = "Signed in. Owner access could not be checked."; });
      };
      updateAccount({ user: window.Clerk.user });
      window.Clerk.addListener(updateAccount);
    } catch (error) {
      showError(error.message || "Could not start secure sign-in. Please try again.");
    }
  }

  async function startPublicPage() {
    try {
      await loadClerk();
      let currentIdentity = identitySignature(window.Clerk.user);
      window.Clerk.addListener(({ user }) => {
        const nextIdentity = identitySignature(user);
        if (nextIdentity !== currentIdentity) window.location.reload();
      });
      if (!window.Clerk.user) return;
      const response = await fetch("/api/owner-auth/session", { credentials: "same-origin" });
      if (!response.ok || !(await response.json()).is_owner) return;
      ownerLink.textContent = "Owner · no limits";
      ownerLink.href = "#";
      ownerLink.addEventListener("click", (event) => event.preventDefault());
      ownerLink.setAttribute("aria-label", "Owner access is active. Sign out.");
      const button = document.createElement("button");
      button.className = "owner-auth-nav-sign-out";
      button.type = "button";
      button.textContent = "Sign out";
      button.style.cssText = "border:0;background:transparent;color:var(--fg-50);font:inherit;font-size:11px;letter-spacing:.12em;text-transform:uppercase;padding:6px 0;cursor:pointer";
      button.addEventListener("click", () => window.Clerk.signOut({ redirectUrl: "/" }));
      ownerLink.insertAdjacentElement("afterend", button);
    } catch (_) {
      // The owner indicator is only convenience UI; privileged checks remain server-side.
    }
  }

  function identitySignature(user) {
    if (!user) return "";
    return [
      user.id || "",
      user.primaryEmailAddressId || "",
      user.primaryEmailAddress?.emailAddress || "",
      user.updatedAt ? new Date(user.updatedAt).getTime() : "",
    ].join(":");
  }

  if (mount && status) startSignInPage();
  else startPublicPage();
})();
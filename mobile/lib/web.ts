import * as WebBrowser from "expo-web-browser";
import { API_BASE } from "./config";

/**
 * Open a public IntelliPlan web page in the in-app browser.
 *
 * For pages that need no sign-in — the privacy policy, the terms, password
 * reset. Password reset stays on the web on purpose: it is guarded by
 * reCAPTCHA and a per-account send limit, and the emailed link opens the
 * web page anyway, so a native copy would be a second, weaker door.
 *
 * Signed-in pages go through startLinkSession instead (see more.tsx).
 */
export type PublicPage = "/forgot-password" | "/privacy" | "/terms" | "/faq";

export async function openPublicPage(path: PublicPage): Promise<void> {
  await WebBrowser.openBrowserAsync(`${API_BASE}${path}`, {
    presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET,
  });
}

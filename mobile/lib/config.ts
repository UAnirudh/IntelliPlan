import { Platform } from "react-native";
import Constants from "expo-constants";

/**
 * Where the app talks to IntelliPlan.
 *
 * Production is the default and the dev-machine addresses apply only under
 * __DEV__. Getting that backwards is invisible while developing — the
 * simulator reaches the dev server and everything works — and fatal the
 * moment the app runs on a real phone, where localhost is the phone
 * itself and every request fails with no obvious cause.
 */

const PROD_BASE = "https://intelliplan.tech";

/** The Flask dev server's port. Metro's own port is not this. */
const DEV_API_PORT = 5000;

/**
 * The dev machine's address, as seen from wherever the app is running.
 *
 * Expo tells the client which host it loaded the bundle from — on a
 * physical phone that is the LAN address of the laptop running
 * `expo start`. Using it means a phone on the same wifi reaches the local
 * Flask server with no configuration at all, which is the case the old
 * hard-coded loopback addresses got wrong: `localhost` on a phone is the
 * phone, and 10.0.2.2 is meaningful only inside an Android emulator.
 *
 * Falls back to the emulator conventions when there is no host to read,
 * which is exactly the simulator case those constants were written for.
 */
function devBase(): string {
  const hostUri =
    (Constants.expoConfig as { hostUri?: string } | null)?.hostUri ||
    (Constants as { expoGoConfig?: { debuggerHost?: string } }).expoGoConfig?.debuggerHost ||
    "";

  // "192.168.1.20:8081" — strip Metro's port, keep the host.
  const host = hostUri.split("/")[0]?.split(":")[0];
  if (host && host !== "localhost" && host !== "127.0.0.1") {
    return `http://${host}:${DEV_API_PORT}`;
  }

  return (
    Platform.select({
      android: `http://10.0.2.2:${DEV_API_PORT}`,
      default: `http://localhost:${DEV_API_PORT}`,
    }) ?? `http://localhost:${DEV_API_PORT}`
  );
}

export const API_BASE =
  process.env.EXPO_PUBLIC_API_BASE || (__DEV__ ? devBase() : PROD_BASE);

import { Platform } from "react-native";

/**
 * Where the app talks to IntelliPlan.
 *
 * The default used to be localhost in every build, including release ones.
 * That is invisible while developing — the simulator reaches the dev server
 * and everything works — and fatal the moment the app runs on a real phone,
 * where localhost is the phone itself and every request fails with no
 * obvious cause.
 *
 * So production is the default, and the dev-machine addresses apply only
 * under __DEV__. Getting it wrong now breaks the simulator, which is loud
 * and immediate, rather than the shipped app, which is silent.
 */

// Android emulators reach the host machine on 10.0.2.2; iOS simulators
// share the host's loopback and can use localhost directly.
const DEV_BASE =
  Platform.select({
    android: "http://10.0.2.2:5000",
    ios: "http://localhost:5000",
    default: "http://localhost:5000",
  }) ?? "http://localhost:5000";

const PROD_BASE = "https://intelliplan.tech";

export const API_BASE =
  process.env.EXPO_PUBLIC_API_BASE || (__DEV__ ? DEV_BASE : PROD_BASE);

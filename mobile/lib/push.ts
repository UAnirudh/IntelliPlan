import { Platform } from "react-native";
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import { registerPushToken, unregisterPushToken } from "./api";

/**
 * Expo push registration.
 *
 * Two things make this fiddly, and both are handled by returning a reason
 * rather than throwing:
 *
 *   * Remote push does not work in Expo Go on Android (SDK 53+). While
 *     you are testing through Expo Go the register call will fail, and
 *     that is expected — not a bug to chase. A development build or a
 *     store build gets a real token.
 *   * A simulator has no push service at all.
 *
 * The token is remembered locally so sign-out can unregister the exact
 * token the server has, instead of asking the OS for it again at a moment
 * when permission may already be gone.
 */

const TOKEN_KEY = "ip_expo_push_token";
const ENABLED_KEY = "ip_push_enabled";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

export type PushResult =
  | { ok: true; token: string }
  | { ok: false; reason: string };

function projectId(): string | undefined {
  const c: any = Constants;
  return (
    c?.expoConfig?.extra?.eas?.projectId ||
    c?.easConfig?.projectId ||
    undefined
  );
}

export async function isPushEnabled(): Promise<boolean> {
  return (await AsyncStorage.getItem(ENABLED_KEY)) === "1";
}

export async function enablePush(): Promise<PushResult> {
  if (!Device.isDevice) {
    return { ok: false, reason: "Push notifications need a real device — a simulator has no push service." };
  }

  if (Platform.OS === "android") {
    // Android drops notifications with no channel. Created before the
    // permission request so the very first notification has somewhere to
    // land.
    await Notifications.setNotificationChannelAsync("default", {
      name: "Deadlines & streaks",
      importance: Notifications.AndroidImportance.DEFAULT,
      vibrationPattern: [0, 250, 250, 250],
    });
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") {
    status = (await Notifications.requestPermissionsAsync()).status;
  }
  if (status !== "granted") {
    return { ok: false, reason: "Notifications are turned off for IntelliPlan in your phone's settings." };
  }

  let token: string;
  try {
    const res = await Notifications.getExpoPushTokenAsync(
      projectId() ? { projectId: projectId() } : undefined,
    );
    token = res.data;
  } catch (e: any) {
    return {
      ok: false,
      reason:
        "Couldn't get a push token. In Expo Go this is expected — remote push needs a development build.",
    };
  }

  try {
    await registerPushToken(token);
  } catch (e: any) {
    return { ok: false, reason: e?.message || "The server wouldn't accept this device's push token." };
  }

  await AsyncStorage.multiSet([
    [TOKEN_KEY, token],
    [ENABLED_KEY, "1"],
  ]);
  return { ok: true, token };
}

export async function disablePush(): Promise<void> {
  const token = await AsyncStorage.getItem(TOKEN_KEY);
  await AsyncStorage.setItem(ENABLED_KEY, "0");
  if (!token) return;
  try {
    await unregisterPushToken(token);
  } finally {
    await AsyncStorage.removeItem(TOKEN_KEY);
  }
}

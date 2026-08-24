import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * Whether this account has been walked through setup.
 *
 * Keyed by user id rather than a single flag: phones get shared between
 * siblings, and a second student signing in should get the tour rather
 * than inheriting the first one's "already done".
 */
const KEY = (userId: number | string) => `ip_onboarded_${userId}`;

export async function hasOnboarded(userId: number | string): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(KEY(userId))) === "1";
  } catch {
    // A storage read that fails should not trap someone in onboarding
    // forever, so the safe default is "they have seen it".
    return true;
  }
}

export async function markOnboarded(userId: number | string): Promise<void> {
  try {
    await AsyncStorage.setItem(KEY(userId), "1");
  } catch {
    /* worst case they see the tour once more */
  }
}

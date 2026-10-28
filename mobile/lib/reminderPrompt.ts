import AsyncStorage from "@react-native-async-storage/async-storage";

const promptKey = (userId: number | string) => `ip_reminder_prompt_answered_${userId}`;

export async function getReminderPromptAnswer(userId: number | string): Promise<string | null> {
  return AsyncStorage.getItem(promptKey(userId));
}

/** Persist the student's explicit reminder choice across onboarding and Today. */
export async function markReminderPromptAnswered(
  userId: number | string | undefined,
  answer: "enabled" | "dismissed",
): Promise<void> {
  if (userId === undefined) return;
  await AsyncStorage.setItem(promptKey(userId), answer);
}

/**
 * Sign-out cancels local reminders so another student cannot receive this
 * account's task details. Forget an enabled answer too, so this student is
 * asked to opt in again when they return and their reminders can be rebuilt.
 */
export async function clearEnabledReminderPromptAnswer(
  userId: number | string | undefined,
): Promise<void> {
  if (userId === undefined) return;
  const key = promptKey(userId);
  if ((await AsyncStorage.getItem(key)) === "enabled") {
    await AsyncStorage.removeItem(key);
  }
}

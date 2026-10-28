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

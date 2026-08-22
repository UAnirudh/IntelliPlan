import { useEffect } from "react";
import { useRouter } from "expo-router";
import { useAuth } from "../lib/auth";
import { hasOnboarded } from "../lib/onboarding";
import { Loading, Screen } from "../components/ui";

/**
 * The gate. Renders a spinner while the stored token is checked against
 * the server, then sends the student to the right place exactly once.
 */
export default function Index() {
  const router = useRouter();
  const { user, booting } = useAuth();

  useEffect(() => {
    if (booting) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    // Onboarding is per-account, so a shared phone gives the second
    // student the tour rather than dropping them into someone else's
    // finished setup.
    hasOnboarded(user.id)
      .then((done) => router.replace(done ? "/(tabs)/today" : "/onboarding"))
      .catch(() => router.replace("/(tabs)/today"));
  }, [booting, user, router]);

  return (
    <Screen>
      <Loading />
    </Screen>
  );
}

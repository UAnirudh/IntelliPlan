import { useEffect } from "react";
import { useRouter } from "expo-router";
import { useAuth } from "../lib/auth";
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
    router.replace(user ? "/(tabs)/today" : "/login");
  }, [booting, user, router]);

  return (
    <Screen>
      <Loading />
    </Screen>
  );
}

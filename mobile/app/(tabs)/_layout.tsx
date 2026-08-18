import { Tabs, useRouter } from "expo-router";
import { Pressable, Text } from "react-native";
import { logout } from "../../lib/api";

/**
 * Tab order follows the day, not the data model: what is due, then the
 * plan for doing it, then where that leaves your grades. Today stays first
 * because it is the answer to "what now", which is why someone opens a
 * planner on a phone at all.
 */
export default function TabsLayout() {
  const router = useRouter();

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: "#1a56db",
        headerRight: () => (
          <Pressable
            onPress={async () => { await logout(); router.replace("/login"); }}
            hitSlop={12}
            style={{ paddingHorizontal: 14 }}
            accessibilityRole="button"
            accessibilityLabel="Sign out"
          >
            <Text style={{ color: "#52524e", fontSize: 14 }}>Sign out</Text>
          </Pressable>
        ),
      }}
    >
      <Tabs.Screen name="today" options={{ title: "Today" }} />
      <Tabs.Screen name="assignments" options={{ title: "Due" }} />
      <Tabs.Screen name="schedule" options={{ title: "Plan" }} />
      <Tabs.Screen name="grades" options={{ title: "Grades" }} />
      <Tabs.Screen name="predictions" options={{ title: "Forecast" }} />
    </Tabs>
  );
}

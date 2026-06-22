import { Tabs } from "expo-router";

export default function TabsLayout() {
  return (
    <Tabs screenOptions={{ tabBarActiveTintColor: "#1a56db" }}>
      <Tabs.Screen name="today" options={{ title: "Today" }} />
      <Tabs.Screen name="predictions" options={{ title: "Predictions" }} />
    </Tabs>
  );
}

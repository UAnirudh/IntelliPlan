import { useEffect } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { getToken } from "../lib/api";

export default function Index() {
  const router = useRouter();
  useEffect(() => {
    (async () => {
      const token = await getToken();
      router.replace(token ? "/(tabs)/today" : "/login");
    })();
  }, []);
  return (
    <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
      <ActivityIndicator />
    </View>
  );
}

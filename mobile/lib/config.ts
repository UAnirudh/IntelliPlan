import { Platform } from "react-native";

// Override per environment. Android emulator uses 10.0.2.2 to reach the host;
// iOS simulator can use localhost directly.
const DEV_BASE = Platform.select({
  android: "http://10.0.2.2:5000",
  ios: "http://localhost:5000",
  default: "http://localhost:5000",
});

export const API_BASE = process.env.EXPO_PUBLIC_API_BASE || DEV_BASE;

import type { Metadata } from "next";
import { Login } from "@/components/site/Site";

export const metadata: Metadata = {
  title: "U-Tax · CEO sign-in",
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  return <Login />;
}

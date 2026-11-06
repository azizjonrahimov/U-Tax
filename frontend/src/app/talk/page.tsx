import type { Metadata } from "next";
import { Talk } from "@/components/talk/Talk";

export const metadata: Metadata = {
  title: "Talk to U-Tax",
  description: "A free Delaware franchise tax estimate from U-Tax's AI intake agent. Educational, not tax advice.",
};

export default function TalkPage() {
  return <Talk />;
}

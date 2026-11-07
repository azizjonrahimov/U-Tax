import type { Metadata } from "next";
import { Ceo } from "@/components/ceo/Ceo";

export const metadata: Metadata = {
  title: "U-Tax · CEO console",
  description: "The CEO's console: the brief, approvals, standing orders, hiring and blocked cases.",
  robots: { index: false, follow: false },
};

export default function CeoPage() {
  return <Ceo />;
}

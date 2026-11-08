import type { Metadata } from "next";
import { Office } from "@/components/office/Office";

export const metadata: Metadata = {
  title: "U-Tax · The office",
  description: "The firm at work: every department's live workspace, drawn from the action log.",
};

export default function OfficePage() {
  return <Office />;
}

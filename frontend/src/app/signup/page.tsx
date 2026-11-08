import type { Metadata } from "next";
import { Signup } from "@/components/site/Site";

export const metadata: Metadata = {
  title: "U-Tax · Get your free checkup",
  description: "Open a free Delaware franchise-tax checkup. We email you for the numbers and send an estimate you can check line by line.",
};

export default function SignupPage() {
  return <Signup />;
}

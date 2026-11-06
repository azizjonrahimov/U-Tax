import { BackendStatus } from "@/components/BackendStatus";

export default function Home() {
  return (
    <main className="flex flex-1 items-center justify-center px-6 py-24">
      <div className="flex max-w-xl flex-col items-center gap-6 text-center">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Naive YC Hackathon
        </h1>
        <p className="text-lg text-zinc-600 dark:text-zinc-400">
          Something is being built here. Check back soon.
        </p>
        <BackendStatus />
      </div>
    </main>
  );
}

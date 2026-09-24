import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const host = requestHeaders.get("x-forwarded-host") ?? requestHeaders.get("host") ?? "localhost:3001";
  const protocol = requestHeaders.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const origin = `${protocol}://${host}`;
  return {
    title: "Deep Space Field Notes",
    description: "An observatory archive of carefully selected Seestar captures with 50 or more stacked frames.",
    openGraph: { title: "Deep Space Field Notes", description: "A cinematic observatory archive of deep-sky captures.", images: [`${origin}/og.png`] },
    twitter: { card: "summary_large_image", title: "Deep Space Field Notes", description: "A cinematic observatory archive of deep-sky captures.", images: [`${origin}/og.png`] },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

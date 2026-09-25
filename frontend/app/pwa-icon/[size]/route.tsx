import { ImageResponse } from "next/og";

/**
 * App icons rendered from the brand mark, so there are no binary image files to keep in sync.
 * `?maskable=1` adds the safe-zone padding Android needs to crop the icon into any shape.
 */
export async function GET(req: Request, { params }: { params: Promise<{ size: string }> }) {
  const { size: raw } = await params;
  const size = [180, 192, 512].includes(Number(raw)) ? Number(raw) : 192;
  const maskable = new URL(req.url).searchParams.has("maskable");
  const inner = maskable ? size * 0.62 : size;
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
                    background: maskable ? "#2a78d6" : "transparent" }}>
        <div style={{ width: inner, height: inner, borderRadius: maskable ? 0 : size * 0.22, background: "#2a78d6",
                      color: "#ffffff", display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: inner * 0.56, fontWeight: 700, fontFamily: "sans-serif" }}>
          L
        </div>
      </div>
    ),
    { width: size, height: size, headers: { "Cache-Control": "public, max-age=604800, immutable" } },
  );
}

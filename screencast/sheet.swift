// Tile JPEG frames into labelled contact sheets with CoreGraphics (no deps).
// Usage: sheet <listfile> <cols> <rows> <outdir> [quality]
//   listfile lines: "<path>\t<label>"   → outdir/sheet_001.jpg, sheet_002.jpg …
import Foundation
import CoreGraphics
import CoreText
import ImageIO
import UniformTypeIdentifiers

let a = CommandLine.arguments
guard a.count >= 5, let cols = Int(a[2]), let rows = Int(a[3]) else {
    FileHandle.standardError.write("usage: sheet <listfile> <cols> <rows> <outdir> [quality]\n".data(using: .utf8)!); exit(2)
}
let quality = a.count >= 6 ? Double(a[5]) ?? 0.82 : 0.82
let outdir = URL(fileURLWithPath: a[4])
try? FileManager.default.createDirectory(at: outdir, withIntermediateDirectories: true)
let items: [(String, String)] = (try! String(contentsOfFile: a[1], encoding: .utf8))
    .split(separator: "\n").compactMap { line in
        let p = line.split(separator: "\t", maxSplits: 1).map(String.init)
        return p.count == 2 ? (p[0], p[1]) : nil
    }
guard !items.isEmpty else { exit(0) }

func load(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}
guard let first = load(items[0].0) else { exit(1) }
let tw = first.width, th = first.height, pad = 6
let W = cols * tw + (cols + 1) * pad, H = rows * th + (rows + 1) * pad
let cs = CGColorSpaceCreateDeviceRGB()
let font = CTFontCreateWithName("Menlo-Bold" as CFString, 26, nil)
let per = cols * rows
var sheetIndex = 0
for start in stride(from: 0, to: items.count, by: per) {
    sheetIndex += 1
    let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0, space: cs,
                        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
    ctx.setFillColor(CGColor(red: 0.12, green: 0.12, blue: 0.12, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))
    for (k, item) in items[start..<min(start + per, items.count)].enumerated() {
        guard let img = load(item.0) else { continue }
        let c = k % cols, r = k / cols
        let x = pad + c * (tw + pad)
        let yTop = pad + r * (th + pad)              // top-left origin
        let rect = CGRect(x: x, y: H - yTop - th, width: tw, height: th)
        ctx.draw(img, in: rect)
        // label box, top-left of tile
        let attr = [kCTFontAttributeName: font, kCTForegroundColorAttributeName: CGColor(gray: 1, alpha: 1)] as CFDictionary
        let line = CTLineCreateWithAttributedString(CFAttributedStringCreate(nil, item.1 as CFString, attr))
        let b = CTLineGetBoundsWithOptions(line, [])
        let bw = b.width + 16, bh = b.height + 10
        ctx.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0.78))
        ctx.fill(CGRect(x: rect.minX, y: rect.maxY - bh, width: bw, height: bh))
        ctx.textPosition = CGPoint(x: rect.minX + 8, y: rect.maxY - bh + 7 - b.minY)
        CTLineDraw(line, ctx)
    }
    let out = outdir.appendingPathComponent(String(format: "sheet_%03d.jpg", sheetIndex))
    let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.jpeg.identifier as CFString, 1, nil)!
    CGImageDestinationAddImage(dest, ctx.makeImage()!, [kCGImageDestinationLossyCompressionQuality: quality] as CFDictionary)
    CGImageDestinationFinalize(dest)
}
print(sheetIndex)

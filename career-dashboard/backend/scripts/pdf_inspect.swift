#!/usr/bin/env swift

import AppKit
import Foundation
import PDFKit

guard CommandLine.arguments.count >= 2 else {
    fputs("usage: pdf_inspect.swift PDF [RENDER_DIR]\n", stderr)
    exit(2)
}

let pdfPath = CommandLine.arguments[1]
let renderDirectory = CommandLine.arguments.count >= 3 ? CommandLine.arguments[2] : nil

guard let document = PDFDocument(url: URL(fileURLWithPath: pdfPath)) else {
    fputs("unable to open PDF: \(pdfPath)\n", stderr)
    exit(1)
}

if let renderDirectory {
    let directoryURL = URL(fileURLWithPath: renderDirectory)
    try FileManager.default.createDirectory(
        at: directoryURL,
        withIntermediateDirectories: true
    )
    for fileURL in try FileManager.default.contentsOfDirectory(
        at: directoryURL,
        includingPropertiesForKeys: nil
    ) where fileURL.lastPathComponent.range(
        of: #"^page-\d+\.png$"#,
        options: .regularExpression
    ) != nil {
        try FileManager.default.removeItem(at: fileURL)
    }
}

var pages: [[String: Any]] = []
var allText = ""

for index in 0..<document.pageCount {
    guard let page = document.page(at: index) else {
        continue
    }

    let bounds = page.bounds(for: .mediaBox)
    let text = page.string ?? ""
    allText += text
    allText += "\n"

    pages.append([
        "number": index + 1,
        "width_points": Double(bounds.width),
        "height_points": Double(bounds.height),
        "text_characters": text.count,
        "text": text
    ])

    if let renderDirectory {
        let targetWidth: CGFloat = 1400
        let targetHeight = targetWidth * bounds.height / bounds.width
        let image = page.thumbnail(
            of: NSSize(width: targetWidth, height: targetHeight),
            for: .mediaBox
        )
        guard
            let tiff = image.tiffRepresentation,
            let bitmap = NSBitmapImageRep(data: tiff),
            let png = bitmap.representation(using: .png, properties: [:])
        else {
            fputs("unable to render page \(index + 1)\n", stderr)
            exit(1)
        }
        let filename = String(format: "page-%02d.png", index + 1)
        let outputURL = URL(fileURLWithPath: renderDirectory).appendingPathComponent(filename)
        try png.write(to: outputURL)
    }
}

let payload: [String: Any] = [
    "page_count": document.pageCount,
    "text_characters": allText.count,
    "metadata": [
        "title": document.documentAttributes?[PDFDocumentAttribute.titleAttribute] as? String ?? "",
        "author": document.documentAttributes?[PDFDocumentAttribute.authorAttribute] as? String ?? "",
        "subject": document.documentAttributes?[PDFDocumentAttribute.subjectAttribute] as? String ?? ""
    ],
    "pages": pages
]

let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))

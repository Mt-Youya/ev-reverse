// Builds a course poster out of the course's own frames, in one of two aspect ratios.
//
// The backdrop is a compromise, not a design: a lesson frame is a screenshot, and a screenshot has a
// menu bar, a watermark and a wall of 12px text. Dimmed it becomes texture, but the thing it is
// evidence *of* disappears. So the frames are shown as frames instead -- the course's own project
// tree as a card, and lesson slides as thumbnails -- and the title sits on a clean field.
//
// System.Drawing rather than an image library: it is on every Windows box, needs no pip, and renders
// CJK text with the fonts the system already has. Not part of the product.
//
//   Poster.exe <frames dir> <output.png> <16x9|4x3> [thumb1,thumb2,...] [collection] [track] [lessons]
//
// The frames dir holds `tree.png` (the project tree) and the thumbnails named by the fourth
// argument, which defaults to `01,03,04,05`. Naming them per collection is deliberate: which lesson
// slides represent a course is a judgement about the course, not something a generator can pick.
//
// The words on the poster are arguments for the same reason. They used to be constants, and the
// second collection's poster came out titled with the first one's name -- the file name was right
// and the picture was wrong, which is the worst way for that to fail.
//
// The two ratios get two layouts rather than one scaled one. In 4:3 there is a third more height and
// the same width, so stacking the title, the slides and the card reads better than stretching the
// wide layout's three columns down a taller page.

using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Poster
{
    static string Title = "Python语言核心精讲";
    static string Track = "AI 大全栈  ·  PYTHON";
    static string Lessons = "32 节完整课程";
    const string Note = "已解密导出 · 可离线播放";

    static readonly Color Ink = Color.FromArgb(10, 16, 26);
    static readonly Color Ink2 = Color.FromArgb(16, 26, 42);
    static readonly Color Accent = Color.FromArgb(76, 154, 255);
    static readonly Color White = Color.FromArgb(246, 249, 255);
    static readonly Color Muted = Color.FromArgb(150, 168, 194);

    static int Width;
    static int Height;
    static bool Wide;
    static string[] Thumbs = { "01", "03", "04", "05" };

    static void Main(string[] args)
    {
        var frames = args.Length > 0 ? args[0] : @"D:\Codes\github\ev-reverse\build\frames";
        var output = args.Length > 1 ? args[1] : @"D:\Codes\github\ev-reverse\build\Python语言核心精讲-封面-16x9.png";
        var ratio = args.Length > 2 ? args[2] : "16x9";
        if (args.Length > 3 && args[3].Trim().Length > 0)
        {
            Thumbs = args[3].Split(',');
        }
        if (args.Length > 4 && args[4].Trim().Length > 0) { Title = args[4]; }
        if (args.Length > 5 && args[5].Trim().Length > 0) { Track = args[5]; }
        if (args.Length > 6 && args[6].Trim().Length > 0) { Lessons = args[6]; }

        Wide = ratio.Replace(":", "x").Replace("X", "x") == "16x9";
        Width = 1600;
        Height = Wide ? 900 : 1200;

        using (var canvas = new Bitmap(Width, Height))
        using (var g = Graphics.FromImage(canvas))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.CompositingQuality = CompositingQuality.HighQuality;

            PaintField(g);
            if (Wide) { WideLayout(g, frames); } else { TallLayout(g, frames); }
            PaintFooter(g);

            Directory.CreateDirectory(Path.GetDirectoryName(output));
            canvas.Save(output, ImageFormat.Png);
            Console.WriteLine("{0}  {1}x{2}", output, Width, Height);
        }
    }

    /// 1600x900: title on the left, the course tree filling the right, the slides in one row beneath.
    ///
    /// One row, not two: the band under the title is 180px tall, and two rows of a readable size do
    /// not fit in it -- the second row was drawn off the bottom of the canvas.
    static void WideLayout(Graphics g, string frames)
    {
        PaintText(g, 94, 200, 800, 72, 56);
        PaintTree(g, Path.Combine(frames, "tree.png"), new Rectangle(1024, 96, 420, 708), Fit.Height);
        // 4 x 210 + 3 x 18 = 894, which is the column's usable width (100..1000).
        PaintRow(g, frames, Thumbs, 100, 596, 210, 118, 18);
    }

    /// 1600x1200: the course tree keeps the right at full height, and the slides fill the taller
    /// left column in a 2x2 grid.
    ///
    /// The title starts at 64px, not the 84 the wide layout asks for. The column is the same 700px
    /// and the card is 84px closer, so the tall layout simply has less room for words; the wide
    /// layout's size is what made "Python Web 后端框架" run under the card here while the 16:9 poster
    /// with the same title was fine.
    static void TallLayout(Graphics g, string frames)
    {
        PaintText(g, 94, 150, 700, 64, 46);
        PaintTree(g, Path.Combine(frames, "tree.png"), new Rectangle(940, 96, 560, 1008), Fit.Height);
        PaintRow(g, frames, Thumbs, 100, 560, 330, 186, 22);
        if (Thumbs.Length > 2)
        {
            PaintRow(g, frames, Thumbs.Skip(2).ToArray(), 100, 560 + 186 + 22, 330, 186, 22);
        }
    }

    static void PaintField(Graphics g)
    {
        using (var baseBrush = new LinearGradientBrush(
            new Rectangle(0, 0, Width, Height), Ink2, Ink, LinearGradientMode.Vertical))
        {
            g.FillRectangle(baseBrush, 0, 0, Width, Height);
        }
        using (var glow = new GraphicsPath())
        {
            glow.AddEllipse(-420, -520, 1500, 1200);
            using (var brush = new PathGradientBrush(glow))
            {
                brush.CenterColor = Color.FromArgb(52, 40, 96, 170);
                brush.SurroundColors = new[] { Color.FromArgb(0, 40, 96, 170) };
                g.FillPath(brush, glow);
            }
        }
    }

    static void PaintText(Graphics g, int x, int y, int column, int want, int least)
    {
        g.FillRectangle(new SolidBrush(Accent), x + 6, y, 96, 6);

        using (var track = new Font("Microsoft YaHei UI", 22, FontStyle.Bold))
        {
            g.DrawString(Track, track, new SolidBrush(Accent), x + 6, y + 34);
        }

        // One font, measured once: the block below the title is placed from that measurement, so the
        // two ratios do not need hand-tuned offsets.
        int after;
        using (var title = FitFont(g, Title, FontStyle.Bold, want, least, column))
        {
            g.DrawString(Title, title, new SolidBrush(White), x, y + 78);
            after = y + 78 + (int)Math.Ceiling(TextRenderer.MeasureText(Title, title).Height * 1.15);
        }

        using (var meta = new Font("Microsoft YaHei UI", 26, FontStyle.Regular))
        {
            g.DrawString(Lessons, meta, new SolidBrush(White), x + 6, after + 6);
        }
        using (var note = new Font("Microsoft YaHei UI", 19, FontStyle.Regular))
        {
            g.DrawString(Note, note, new SolidBrush(Muted), x + 6, after + 52);
        }
    }

    /// The largest font at or below `want` whose rendering of `text` fits `maxWidth`.
    ///
    /// Measured with `TextRenderer`, not `Graphics.MeasureString`: GDI+ under-measures a mixed
    /// Latin/CJK run, and the title was drawn ~15% wider than it claimed -- which is how it ended up
    /// under the card in both layouts while the measurement said it fit. The 90% budget is the same
    /// lesson applied once more: leave the layout a margin the measurement cannot eat.
    static Font FitFont(Graphics g, string text, FontStyle style, int want, int least, int maxWidth)
    {
        var budget = (int)(maxWidth * 0.9);
        for (var size = want; size > least; size -= 2)
        {
            var font = new Font("Microsoft YaHei UI", size, style);
            if (TextRenderer.MeasureText(text, font).Width <= budget)
            {
                return font;
            }
            font.Dispose();
        }
        return new Font("Microsoft YaHei UI", least, style);
    }

    static void PaintTree(Graphics g, string path, Rectangle card, Fit fit)
    {
        if (!File.Exists(path)) { Console.Error.WriteLine("no tree frame: " + path); return; }
        DrawCard(g, path, card, fit);
    }

    static void PaintRow(Graphics g, string dir, string[] names, int x, int y, int w, int h, int gap)
    {
        foreach (var name in names)
        {
            var path = Path.Combine(dir, name.EndsWith(".png") ? name : name + ".png");
            if (!File.Exists(path)) { Console.Error.WriteLine("no thumbnail: " + path); continue; }
            DrawCard(g, path, new Rectangle(x, y, w, h), Fit.Width);
            using (var pen = new Pen(Color.FromArgb(70, 120, 160, 210), 1))
            {
                g.DrawRectangle(pen, x, y, w, h);
            }
            x += w + gap;
        }
    }

    static void PaintFooter(Graphics g)
    {
        using (var line = new Pen(Color.FromArgb(40, 120, 160, 210), 1))
        {
            g.DrawLine(line, 100, Height - 76, Width - 100, Height - 76);
        }
        using (var brand = new Font("Microsoft YaHei UI", 19, FontStyle.Bold))
        {
            g.DrawString("evmedia", brand, new SolidBrush(Color.FromArgb(140, 160, 190)), Width - 216, Height - 56);
        }
    }

    enum Fit { Width, Height }

    /// A rounded, shadowed card holding one image, cropped to that card's shape.
    ///
    /// `fit` decides which dimension the image is scaled by. The tree frame is tall and the card is
    /// taller still, so scaling it by width would push most of the lesson list out of the card.
    static void DrawCard(Graphics g, string imagePath, Rectangle card, Fit fit)
    {
        using (var path = Rounded(card, 10))
        {
            for (var i = 6; i >= 1; i--)
            {
                using (var shadow = Rounded(new Rectangle(card.X - i / 2, card.Y + i, card.Width + i, card.Height + i), 10 + i))
                using (var pen = new Pen(Color.FromArgb(10, 0, 0, 0), i * 1.6f))
                {
                    g.DrawPath(pen, shadow);
                }
            }

            using (var image = Image.FromFile(imagePath))
            {
                var state = g.Save();
                g.SetClip(path);

                float w, h;
                if (fit == Fit.Width)
                {
                    var scale = (float)card.Width / image.Width;
                    w = card.Width;
                    h = image.Height * scale;
                }
                else
                {
                    var scale = (float)card.Height / image.Height;
                    h = card.Height;
                    w = image.Width * scale;
                }
                g.DrawImage(image, new RectangleF(card.X, card.Y, w, h));
                g.Restore(state);
            }

            using (var border = new Pen(Color.FromArgb(90, 120, 160, 210), 1.4f))
            {
                g.DrawPath(border, path);
            }
        }
    }

    static GraphicsPath Rounded(Rectangle r, int radius)
    {
        var path = new GraphicsPath();
        var d = radius * 2;
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}

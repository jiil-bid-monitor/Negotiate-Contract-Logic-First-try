import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;

/** 옛 한글(.hwp) → HWPX.  java -jar hwp2hwpx-cli.jar 원본.hwp 결과.hwpx
 *  변환은 공개 라이브러리 hwp2hwpx·hwplib·hwpxlib(Apache-2.0, neolord0)가 함 — 이 파일은 명령줄 입구만. */
public class Convert {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("사용법: java -jar hwp2hwpx-cli.jar 원본.hwp 결과.hwpx");
            System.exit(2);
        }
        HWPFile from = HWPReader.fromFile(args[0]);
        HWPXFile to = Hwp2Hwpx.toHWPX(from);
        HWPXWriter.toFilepath(to, args[1]);
    }
}

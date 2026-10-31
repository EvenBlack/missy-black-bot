"""Deterministic image edits. No API, network client or model is used here."""
import math
import re
from io import BytesIO
from PIL import Image, ImageEnhance, ImageOps, ImageDraw, ImageFont, UnidentifiedImageError

FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_EDGE = 3072


class EditError(ValueError):
    pass


def parse_edit(text):
    text = text.strip()
    if not text:
        raise EditError('Вкажи правку: світліше, темніше, тепліше, контраст, обрізати 1:1 або напис Текст.')
    operation, _, value = text.partition(' ')
    operation = operation.lower()
    value = value.strip()
    aliases = {'світліше':'lighten','темніше':'darken','тепліше':'warm','холодніше':'cool','контраст':'contrast','яскравість':'brightness','обрізати':'crop','напис':'text'}
    operation = aliases.get(operation, operation)
    if operation in {'lighten','darken','warm','cool'}:
        if value:
            raise EditError('Для цієї правки достатньо одного слова. Наступну правку можна застосувати відповіддю на результат.')
        return operation, None
    if operation in {'contrast','brightness'}:
        try:
            factor = float(value.replace(',', '.')) if value else 1.2
        except ValueError:
            raise EditError('Вкажи число від 0.2 до 3, наприклад: контраст 1.2.') from None
        if not math.isfinite(factor) or not 0.2 <= factor <= 3:
            raise EditError('Допустиме значення — від 0.2 до 3.')
        return operation, factor
    if operation == 'crop':
        match = re.fullmatch(r'(\d{1,3})\s*:\s*(\d{1,3})', value)
        if not match:
            raise EditError('Вкажи пропорцію, наприклад: обрізати 1:1 або обрізати 16:9.')
        width, height = map(int,match.groups())
        if not width or not height or not 0.2 <= width / height <= 5:
            raise EditError('Пропорція має бути між 1:5 та 5:1.')
        return operation, width / height
    if operation == 'text':
        if not value or len(value) > 200 or any(not c.isprintable() and c != '\n' for c in value):
            raise EditError('Для напису потрібен текст від 1 до 200 символів.')
        return operation, value
    raise EditError('Ця правка поки не підтримується. Доступні: світліше, темніше, тепліше, холодніше, контраст, яскравість, обрізати, напис.')


def add_text(image, text):
    if image.width < 100 or image.height < 100:
        raise EditError('Для напису потрібне зображення не менше 100×100 пікселів.')
    try:
        font = ImageFont.truetype(FONT_PATH, max(12,min(100,image.width//22)))
    except OSError:
        raise EditError('Шрифт для напису недоступний. Інші правки можна використовувати.') from None
    padding = max(8,image.width//50)
    draw = ImageDraw.Draw(image)
    lines, current = [], ''
    for char in text:
        if char == '\n':
            lines.append(current);current='';continue
        proposed = current + char
        if draw.textbbox((0,0),proposed,font=font)[2] > image.width - padding*2:
            lines.append(current.rstrip());current=char.lstrip()
        else:
            current=proposed
    if current:lines.append(current)
    line_height = font.getbbox('ЇҐАg')[3] + max(3,font.size//5)
    panel_height = line_height*len(lines)+padding*2
    if panel_height > image.height//2:
        raise EditError('Напис завеликий для цього фото. Скороти текст.')
    overlay=Image.new('RGBA',image.size,(0,0,0,0))
    painter=ImageDraw.Draw(overlay)
    y=image.height-panel_height
    painter.rectangle((0,y,image.width,image.height),fill=(0,0,0,180))
    for line in lines:
        painter.text((padding,y+padding),line,font=font,fill=(255,255,255,255))
        y+=line_height
    return Image.alpha_composite(image.convert('RGBA'),overlay)


def edit_image(data, specification):
    operation,value=parse_edit(specification)
    if not data or len(data)>MAX_BYTES:
        raise EditError('Для редагування потрібне зображення до 8 МіБ.')
    try:
        with Image.open(BytesIO(data)) as source:
            if source.width*source.height>MAX_PIXELS:
                raise EditError('Для локального редактора поки підтримуються фото до 16 мегапікселів.')
            if getattr(source,'n_frames',1)>1:
                raise EditError('Анімовані та багатосторінкові зображення поки не редагуються.')
            if source.format not in {'JPEG','PNG','WEBP','BMP','TIFF','GIF'}:
                raise EditError('Непідтримуваний формат зображення.')
            resized=max(source.size)>MAX_EDGE
            source.thumbnail((MAX_EDGE,MAX_EDGE),Image.Resampling.LANCZOS)
            oriented=ImageOps.exif_transpose(source)
            alpha='A' in oriented.getbands() or 'transparency' in oriented.info
            image=oriented.convert('RGBA' if alpha else 'RGB')
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError):
        raise EditError('Не вдалося прочитати файл як зображення.') from None
    if operation in {'lighten','darken','brightness'}:
        factor=1.2 if operation=='lighten' else 0.8 if operation=='darken' else value
        image=ImageEnhance.Brightness(image).enhance(factor)
    elif operation=='contrast':
        image=ImageEnhance.Contrast(image).enhance(value)
    elif operation in {'warm','cool'}:
        channels=list(image.split())
        red,blue=(1.08,0.92) if operation=='warm' else (0.92,1.08)
        channels[0]=channels[0].point([min(255,round(i*red)) for i in range(256)])
        channels[2]=channels[2].point([min(255,round(i*blue)) for i in range(256)])
        image=Image.merge(image.mode,channels)
    elif operation=='crop':
        width,height=image.size
        if width/height>value:
            new_width=max(1,round(height*value));left=(width-new_width)//2
            image=image.crop((left,0,left+new_width,height))
        else:
            new_height=max(1,round(width/value));top=(height-new_height)//2
            image=image.crop((0,top,width,top+new_height))
    elif operation=='text':
        image=add_text(image,value)
        if not alpha:image=image.convert('RGB')
    buffer=BytesIO();image.save(buffer,format='PNG')
    output=buffer.getvalue();ext='png'
    if len(output)>MAX_BYTES and not alpha:
        buffer=BytesIO();image.convert('RGB').save(buffer,format='JPEG',quality=92)
        output=buffer.getvalue();ext='jpg'
    if len(output)>MAX_BYTES:
        raise EditError('Результат перевищує 8 МіБ. Спробуй менше зображення.')
    return output,ext,resized

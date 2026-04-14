from PIL import Image
import os
os.makedirs('extension/icons', exist_ok=True)
for size in [16, 32, 48, 128]:
    img = Image.open('static/icons/icon-192.png').resize((size, size), Image.LANCZOS)
    img.save(f'extension/icons/icon-{size}.png')
print('Icons created')
name: Publish Furniture Content

on:
  workflow_dispatch:      # Запуск повністю передано в руки зовнішніх API-запитів
    inputs:
      forced_mode:
        description: 'Режим публікації'
        required: true
        default: 'fb_post'
        type: choice
        options:
          - fb_post
          - ig_post
          - ig_story
    
env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: 'true'
  
jobs:
  publish:
    runs-on: ubuntu-latest

    steps:
    - name: Перевірка коду репозиторію
      uses: actions/checkout@v4

    - name: Налаштування Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.12'

    - name: Встановлення системних залежностей (FFmpeg)
      run: |
        sudo apt-get update
        sudo apt-get install -y ffmpeg

    - name: Встановлення бібліотек Python
      run: |
        python -m pip install --upgrade pip
        pip install requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client pillow pillow-heif pytz pyyaml

    - name: Запуск публікатора контенту
      env:
        GDRIVE_SERVICE_ACCOUNT_KEY: ${{ secrets.GDRIVE_SERVICE_ACCOUNT_KEY }}
        GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        IMGBB_API_KEY: ${{ secrets.IMGBB_API_KEY }}
        IMAGEKIT_PRIVATE_KEY: ${{ secrets.IMAGEKIT_PRIVATE_KEY }}
        # Секрети Власні Меблі:
        IG_USER_ID: ${{ secrets.MEBLI_IG_USER_ID }}
        FB_PAGE_ID: ${{ secrets.MEBLI_FB_PAGE_ID }}
        META_ACCESS_TOKEN: ${{ secrets.MEBLI_META_ACCESS_TOKEN }}
        # Прокидаємо input з GitHub Actions у системну змінну для Python
        FORCED_MODE: ${{ github.event.inputs.forced_mode }}
      run: |
        python << 'EOF'
        import os
        import subprocess

        # Отримуємо режим та назву вкладки
        mode = os.environ.get('FORCED_MODE', 'fb_post')
        target_tab = "Меблі"
        
        print(f"🚀 [Система] Зовнішній запуск API успішний!")
        print(f"📋 [Система] Режим: [{mode.upper()}], Аркуш: [{target_tab}]")
        
        # Динамічний вибір скрипта залежно від режиму
        if mode == 'ig_story':
            script_name = 'publish_content_mebli_srorys.py'
        else:
            script_name = 'publish_content_mebli.py'
            
        print(f"🏃‍♂️ [Система] Запуск скрипта: {script_name}...")
        
        # Передаємо параметри у вибраний скрипт
        subprocess.run(['python', script_name, mode, target_tab], check=True)
        EOF

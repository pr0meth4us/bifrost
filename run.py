from bifrost import create_app
from config import Config

app = create_app(Config)

import os

if __name__ == '__main__':
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1')
    app.run(host='0.0.0.0', port=5000, debug=is_debug)
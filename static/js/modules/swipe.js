// static/js/modules/swipe.js

// Swipe Navigation State & Logic

// Elements - Assigned from app.js
let contentWrapper = null;
let swipeSections = null;
let numSections = 0;

// State Variables - Index is managed by app.js via getter/setter
let touchStartX = 0;
let touchStartY = 0;
let touchCurrentX = 0;
let isSwiping = false;
let horizontalSwipeConfirmed = false;
const swipeThreshold = 50;

// --- Getter/Setter Callbacks provided by app.js ---
let getCurrentSectionIndexCallback = () => 0;
let setCurrentSectionIndexCallback = (index) => {};
// --- End Callbacks ---

/**
 * Initializes swipe module.
 * @param {HTMLElement} wrapper
 * @param {NodeList} sections
 * @param {function} getIndexFunc
 * @param {function} setIndexFunc
 */
export function initSwipe(wrapper, sections, getIndexFunc, setIndexFunc) {
    contentWrapper = wrapper;
    swipeSections = sections;
    numSections = sections.length;
    getCurrentSectionIndexCallback = getIndexFunc;
    setCurrentSectionIndexCallback = setIndexFunc;
    console.log(`Swipe module initialized with ${numSections} sections.`);
    updateContentWrapperTransform(getCurrentSectionIndexCallback(), false); // Initial set without transition
}


/**
 * TouchStart event handler.
 * @param {TouchEvent} event
 */
export function handleTouchStart(event) {
    if (!contentWrapper) return;
    const target = event.target;
    const isInteractive = target.closest('button, input, textarea, select, a, .modal-content');
    const isInsideScrollable = target.closest('.message-area, #agent-status-content, #config-content');
    const isModalOpen = document.querySelector('.modal[style*="display: block"]');

    if (isInteractive || isModalOpen || isInsideScrollable) {
        isSwiping = false;
        console.log(`Swipe ignored (target: ${target.tagName}, interact: ${!!isInteractive}, insideScroll: ${!!isInsideScrollable}, modal: ${!!isModalOpen})`);
        return;
    }
    try {
        touchStartX = event.touches[0].clientX;
        touchStartY = event.touches[0].clientY;
        touchCurrentX = touchStartX;
        isSwiping = true;
        horizontalSwipeConfirmed = false;
        contentWrapper.style.transition = 'none'; // Disable transition during drag
        console.log(`TouchStart: startX=${touchStartX.toFixed(0)}`);
    } catch (e) { console.error("Error in touchstart:", e); isSwiping = false; }
}

/**
 * TouchMove event handler.
 * @param {TouchEvent} event
 */
export function handleTouchMove(event) {
    if (!isSwiping || !contentWrapper) return;
    try {
        const currentY = event.touches[0].clientY;
        touchCurrentX = event.touches[0].clientX;
        const diffX = touchCurrentX - touchStartX;
        const diffY = currentY - touchStartY;

        if (!horizontalSwipeConfirmed) {
            if (Math.abs(diffX) > Math.abs(diffY) + 5) { // Horizontal dominant
                horizontalSwipeConfirmed = true; console.log("Horizontal swipe confirmed.");
            } else if (Math.abs(diffY) > Math.abs(diffX) + 5) { // Vertical dominant
                isSwiping = false; console.log("Vertical scroll detected, canceling swipe."); return;
            }
        }
        if (horizontalSwipeConfirmed) {
            event.preventDefault(); // Prevent scroll ONLY when confirmed horizontal
            const currentIdx = getCurrentSectionIndexCallback();
            const baseTranslateXPercent = -currentIdx * 100;
            // Apply temporary transform during move
            contentWrapper.style.transform = `translateX(calc(${baseTranslateXPercent}% + ${diffX}px))`;
        }
    } catch (e) { console.error("Error in touchmove:", e); isSwiping = false; }
}

/**
 * TouchEnd event handler - **REFINED LOGIC**
 * @param {TouchEvent} event
 */
export function handleTouchEnd(event) {
    // Ensure swipe started and wrapper exists
    if (!isSwiping || !contentWrapper) {
        // Reset just in case, though should already be false if swipe was cancelled
        isSwiping = false;
        horizontalSwipeConfirmed = false;
        return;
    }

    const wasHorizontal = horizontalSwipeConfirmed; // Store confirmation state
    // --- Reset flags immediately ---
    isSwiping = false;
    horizontalSwipeConfirmed = false;
    // --- Re-enable smooth transition for the snap/slide ---
    contentWrapper.style.transition = 'transform 0.3s ease-in-out';

    let currentIdx = getCurrentSectionIndexCallback(); // Get index BEFORE potential change
    let targetIndex = currentIdx; // Target index defaults to current index (snap back)

    if (wasHorizontal) {
        const diffX = touchCurrentX - touchStartX;
        console.log(`TouchEnd (H): Index BEFORE=${currentIdx}, diffX=${diffX.toFixed(0)}, thres=${swipeThreshold}`);

        // Determine if swipe was significant enough to change section
        if (Math.abs(diffX) > swipeThreshold) {
            if (diffX < 0) { // Swipe Left
                targetIndex = Math.min(currentIdx + 1, numSections - 1); // Go to next, but clamp at max index
                console.log(`Swipe Left -> Target Index: ${targetIndex}`);
            } else if (diffX > 0) { // Swipe Right
                targetIndex = Math.max(currentIdx - 1, 0); // Go to previous, but clamp at min index (0)
                console.log(`Swipe Right -> Target Index: ${targetIndex}`);
            }
        } else {
            console.log("Swipe distance below threshold. Snapping back.");
            // Target index remains currentIdx
        }
    } else {
         console.log("TouchEnd: No horizontal swipe confirmed. Snapping back.");
         // Target index remains currentIdx
    }

    // --- Update state in app.js and apply final transform ---
    console.log(`TouchEnd: Setting final index to: ${targetIndex}`);
    setCurrentSectionIndexCallback(targetIndex); // Update the state managed by app.js
    updateContentWrapperTransform(targetIndex, true); // Apply the final transform WITH transition
}


/**
 * Updates the CSS transform. Now takes index directly.
 * @param {number} index - The index of the section to display.
 * @param {boolean} [useTransition=true]
 */
export function updateContentWrapperTransform(index, useTransition = true) {
    // Clamp index just to be safe, although handleTouchEnd should already do it
    const clampedIndex = Math.max(0, Math.min(numSections - 1, index));

    if (contentWrapper && typeof clampedIndex === 'number') {
        const newTranslateXPercent = -clampedIndex * 100;
        console.log(`---> Applying transform: Index=${clampedIndex}, TranslateX=${newTranslateXPercent}% (transition: ${useTransition})`);
        // Set transition property explicitly BEFORE changing transform if requested
        contentWrapper.style.transition = useTransition ? 'transform 0.3s ease-in-out' : 'none';
        contentWrapper.style.transform = `translateX(${newTranslateXPercent}%)`;
    } else {
        console.error(`Invalid call to updateContentWrapperTransform: Index=${index}, Clamped=${clampedIndex}, Wrapper=${!!contentWrapper}`);
    }
}

/**
 * Adds keyboard navigation listeners.
 * @param {function} getIndex - Function to get the current section index from app.js.
 * @param {function} setIndex - Function to set the new section index in app.js.
 */
export function addKeyboardNavListeners(getIndex, setIndex) {
     document.addEventListener('keydown', (e) => {
        const targetTagName = document.activeElement?.tagName.toLowerCase();
        const isModalOpen = document.querySelector('.modal[style*="display: block"]');
        const isInputFocused = ['textarea', 'input', 'select'].includes(targetTagName);
        if (isModalOpen || isInputFocused) return;

        let currentIndex = getIndex();
        let newIndex = currentIndex; // Start with current

        if (e.key === 'ArrowLeft' && currentIndex > 0) {
            newIndex = currentIndex - 1;
            console.log(`Key Left -> New Index: ${newIndex}`);
        } else if (e.key === 'ArrowRight' && currentIndex < numSections - 1) {
            newIndex = currentIndex + 1;
             console.log(`Key Right -> New Index: ${newIndex}`);
        }

        // If index changed, update state and view
        if (newIndex !== currentIndex) {
            setIndex(newIndex); // Update state in app.js
            updateContentWrapperTransform(newIndex, true); // Update view
        }
    });
    console.log("Keyboard navigation listeners added.");
}
